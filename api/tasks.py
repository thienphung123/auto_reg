from datetime import datetime, timezone
import asyncio
import json
import logging
import os
import threading
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from core.db import TaskLog, engine
from services.worker_control import get_worker_state, is_worker_paused

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)

_tasks: dict = {}
_tasks_lock = threading.Lock()
# Serial locks removed — Playwright workers now run in parallel
# _platform_serial_locks: dict[str, threading.Lock] = {"fotor": threading.Lock()}

MAX_FINISHED_TASKS = 200


class RegisterTaskRequest(BaseModel):
    platform: str
    email: Optional[str] = None
    password: Optional[str] = None
    count: int = Field(default=1, ge=1, le=1000)
    concurrency: int = Field(default=1, ge=1, le=10)
    register_delay_seconds: float = Field(default=0, ge=0)
    random_delay_min: Optional[float] = Field(default=None, ge=0)
    random_delay_max: Optional[float] = Field(default=None, ge=0)
    proxy: Optional[str] = None
    executor_type: str = "protocol"
    captcha_solver: str = "yescaptcha"
    extra: dict = Field(default_factory=dict)
    task_id: Optional[str] = None
    interval_type: Optional[str] = None
    interval_value: Optional[int] = None


class TaskLogBatchDeleteRequest(BaseModel):
    ids: list[int]


def _cleanup_old_tasks():
    with _tasks_lock:
        finished = [
            (tid, t)
            for tid, t in _tasks.items()
            if t.get("status") in ("done", "failed")
        ]
        if len(finished) <= MAX_FINISHED_TASKS:
            return
        finished.sort(key=lambda x: x[0])
        for tid, _ in finished[: len(finished) - MAX_FINISHED_TASKS]:
            del _tasks[tid]


def get_runtime_task_snapshot() -> dict:
    with _tasks_lock:
        snapshot = {tid: dict(task) for tid, task in _tasks.items()}
    counts = {
        "pending": 0,
        "running": 0,
        "done": 0,
        "failed": 0,
    }
    for task in snapshot.values():
        status = str(task.get("status", "pending"))
        if status in counts:
            counts[status] += 1
    return {
        "counts": counts,
        "active": counts["pending"] + counts["running"],
        "tasks": snapshot,
        "worker_state": get_worker_state(),
    }


def _log(task_id: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].setdefault("logs", []).append(entry)
    print(entry)


def _save_task_log(platform: str, email: str, status: str, error: str = "", detail: dict = None):
    with Session(engine) as s:
        log = TaskLog(
            platform=platform,
            email=email,
            status=status,
            error=error,
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
        )
        s.add(log)
        s.commit()


def _auto_upload_integrations(task_id: str, account):
    try:
        from services.external_sync import sync_account

        for result in sync_account(account):
            name = result.get("name", "Auto Upload")
            ok = bool(result.get("ok"))
            msg = result.get("msg", "")
            _log(task_id, f"  [{name}] {'[OK] ' + msg if ok else '[FAIL] ' + msg}")
    except Exception as e:
        _log(task_id, f"  [Auto Upload] exception: {e}")


def _log_system_metrics(task_id: str):
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.3)
        process = psutil.Process(os.getpid())
        used_bytes = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                used_bytes += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        used_gb = round(used_bytes / (1024 ** 3), 2)
        total_gb = 16.0
        ram_percent = round((used_gb / total_gb) * 100, 1)
        _log(task_id, f"[SYSTEM METRICS] CPU: {cpu}% | RAM: {ram_percent}% ({used_gb}GB / 16.0GB)")
    except ImportError:
        _log(task_id, "[SYSTEM METRICS] psutil not installed — metrics unavailable")
    except Exception as e:
        _log(task_id, f"[SYSTEM METRICS] error: {e}")


def _run_register(task_id: str, req: RegisterTaskRequest):
    from core.base_mailbox import create_mailbox
    from core.base_platform import RegisterConfig
    from core.db import (
        get_fotor_ref_parent,
        increment_referral_count,
        release_fotor_ref_parent,
        release_fotor_ref_claim,
        save_account,
    )
    from core.registry import get

    with _tasks_lock:
        _tasks[task_id]["status"] = "running"

    success = 0
    errors = []
    start_gate_lock = threading.Lock()
    next_start_time = time.time()

    try:
        PlatformCls = get(req.platform)

        def _build_mailbox(proxy: Optional[str]):
            from core.config_store import config_store

            merged_extra = config_store.get_all().copy()
            merged_extra.update({k: v for k, v in req.extra.items() if v is not None and v != ""})
            return create_mailbox(
                provider=merged_extra.get("mail_provider", "laoudo"),
                extra=merged_extra,
                proxy=proxy,
            )

        def _do_one(i: int):
            nonlocal next_start_time
            _proxy = None
            try:
                from core.proxy_pool import proxy_pool
                from core.config_store import config_store

                _proxy = req.proxy or proxy_pool.get_next()

                if req.register_delay_seconds > 0 or (
                    req.random_delay_min is not None and req.random_delay_max is not None
                ):
                    with start_gate_lock:
                        now = time.time()
                        wait_seconds = max(0.0, next_start_time - now)
                        if req.register_delay_seconds > 0 and wait_seconds > 0:
                            _log(task_id, f"Delay before account {i + 1}: {wait_seconds:g}s")
                            time.sleep(wait_seconds)
                        next_start_time = time.time() + req.register_delay_seconds

                        if req.random_delay_min is not None and req.random_delay_max is not None:
                            import random

                            random_delay = random.uniform(req.random_delay_min, req.random_delay_max)
                            if random_delay > 0:
                                _log(task_id, f"Random delay before account {i + 1}: {random_delay:.1f}s")
                                time.sleep(random_delay)
                            next_start_time = time.time() + random_delay

                merged_extra = config_store.get_all().copy()
                merged_extra.update({k: v for k, v in req.extra.items() if v is not None and v != ""})
                selected_parent_email = "MASTER"
                reserved_parent = False

                if req.platform == "fotor":
                    master_ref_link = (
                        merged_extra.get("fotor_ref_link")
                        or merged_extra.get("ref_link")
                        or "https://www.fotor.com/referrer/ce1yh8e7"
                    )
                    selected_parent_email, selected_ref_link = get_fotor_ref_parent(master_ref_link)
                    reserved_parent = selected_parent_email != "MASTER"
                    merged_extra["fotor_ref_link"] = selected_ref_link
                    merged_extra["parent_email"] = selected_parent_email
                    _log(task_id, f"[FOTOR_REF] parent={selected_parent_email} ref={selected_ref_link}")

                worker_label = f"Worker-{i + 1}"
                if req.platform == "fotor" and reserved_parent:
                    with _tasks_lock:
                        _tasks[task_id]["worker_status"] = f"{worker_label}: Đang ôm Acc Cha {selected_parent_email}"
                    _log(task_id, f"[LOCK] {worker_label} claimed parent: {selected_parent_email}")

                try:
                    _config = RegisterConfig(
                        executor_type=req.executor_type,
                        captcha_solver=req.captcha_solver,
                        proxy=_proxy,
                        extra=merged_extra,
                    )
                    _mailbox = _build_mailbox(_proxy)
                    _platform = PlatformCls(config=_config, mailbox=_mailbox)
                    _platform._log_fn = lambda msg: _log(task_id, msg)
                    if getattr(_platform, "mailbox", None) is not None:
                        _platform.mailbox._log_fn = _platform._log_fn

                    with _tasks_lock:
                        _tasks[task_id]["progress"] = f"{i + 1}/{req.count}"

                    _log(task_id, f"Starting account {i + 1}/{req.count}")
                    if _proxy:
                        _log(task_id, f"Proxy: {_proxy}")

                    # Log system metrics before launching Playwright
                    _log_system_metrics(task_id)

                    account = _platform.register(email=req.email or None, password=req.password)

                    # Log system metrics after registration completes
                    _log_system_metrics(task_id)

                    if isinstance(account.extra, dict):
                        account.extra["referred_count"] = 0
                        account.extra["parent_email"] = account.extra.get("parent_email") or selected_parent_email
                        mail_provider = merged_extra.get("mail_provider", "")
                        if mail_provider:
                            account.extra["mail_provider"] = mail_provider
                        if req.platform == "fotor" and mail_provider == "tempmail_lol":
                            _log(task_id, "[WARN] tempmail_lol is deprecated for Fotor scheduled tasks.")

                    saved_account = save_account(account)
                    if req.platform == "fotor":
                        increment_referral_count((account.extra or {}).get("parent_email", selected_parent_email))

                    if _proxy:
                        proxy_pool.report_success(_proxy)
                    _log(task_id, f"[OK] Registration success: {account.email}")
                    _save_task_log(req.platform, account.email, "success")
                    _auto_upload_integrations(task_id, saved_account or account)

                    cashier_url = (account.extra or {}).get("cashier_url", "")
                    if cashier_url:
                        _log(task_id, f"[Cashier] {cashier_url}")
                        with _tasks_lock:
                            _tasks[task_id].setdefault("cashier_urls", []).append(cashier_url)
                    return True
                except Exception as e:
                    # === 402 Auto-Ban: Trảm proxy hết băng thông ===
                    err_msg = str(e)
                    is_402 = (
                        "402" in err_msg
                        or "Payment Required" in err_msg
                        or "ProxyBandwidthExhausted" in type(e).__name__
                    )
                    if is_402 and _proxy:
                        try:
                            from core.proxy_pool import proxy_pool
                            proxy_pool.ban_proxy(_proxy)
                            _log(task_id, f"[PROXY DEAD] Băng thông cạn (402), đã loại bỏ proxy: {_proxy}")
                        except Exception:
                            pass

                    try:
                        if req.platform == "fotor":
                            release_fotor_ref_parent(selected_parent_email)
                    except Exception:
                        pass
                    try:
                        if _proxy and not is_402:
                            from core.proxy_pool import proxy_pool

                            proxy_pool.report_fail(_proxy)
                    except Exception:
                        pass
                    _log(task_id, f"[FAIL] Registration failed: {e}")
                    _save_task_log(req.platform, req.email or "", "failed", error=str(e))
                    return str(e)
                finally:
                    # Always release in-memory parent claim
                    if req.platform == "fotor" and reserved_parent:
                        release_fotor_ref_claim(selected_parent_email)
                        _log(task_id, f"[UNLOCK] {worker_label} released parent: {selected_parent_email}")
                    with _tasks_lock:
                        _tasks[task_id].pop("worker_status", None)
            except Exception as e:
                _log(task_id, f"[FAIL] Setup failed: {e}")
                _save_task_log(req.platform, req.email or "", "failed", error=str(e))
                return str(e)

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=1) as pool:
            futures = [pool.submit(_do_one, i) for i in range(req.count)]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as e:
                    _log(task_id, f"[ERROR] Worker exception: {e}")
                    errors.append(str(e))
                    continue
                if result is True:
                    success += 1
                else:
                    errors.append(result)
    except Exception as e:
        _log(task_id, f"Fatal error: {e}")
        with _tasks_lock:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = str(e)
        return

    with _tasks_lock:
        _tasks[task_id]["status"] = "done"
        _tasks[task_id]["success"] = success
        _tasks[task_id]["errors"] = errors
    _log(task_id, f"Completed: success {success}, failed {len(errors)}")
    _cleanup_old_tasks()


def _serialize_scheduled_task(task) -> dict:
    return {
        "task_id": task.task_id,
        "platform": task.platform,
        "count": task.count,
        "executor_type": task.executor_type,
        "captcha_solver": task.captcha_solver,
        "extra": task.get_extra(),
        "interval_type": task.interval_type,
        "interval_value": task.interval_value,
        "paused": task.paused,
    }


def _sanitize_task_payload(platform: str, extra: dict) -> dict:
    clean_extra = dict(extra or {})
    if str(platform or "").strip().lower() == "fotor" and clean_extra.get("mail_provider") == "tempmail_lol":
        from core.config_store import config_store

        fallback_provider = str(config_store.get("mail_provider", "duckmail") or "duckmail").strip().lower()
        if fallback_provider == "tempmail_lol":
            fallback_provider = "duckmail"
        clean_extra["mail_provider"] = fallback_provider
    return clean_extra


@router.post("/register")
def create_register_task(req: RegisterTaskRequest, background_tasks: BackgroundTasks):
    if is_worker_paused():
        state = get_worker_state()
        detail = state.get("reason") or "Workers are paused"
        raise HTTPException(409, f"Worker paused: {detail}")

    mail_provider = req.extra.get("mail_provider")
    if mail_provider == "luckmail":
        platform = req.platform
        if platform in ("tavily", "openblocklabs"):
            raise HTTPException(400, f"LuckMail does not support {platform}")

        mapping = {
            "trae": "trae",
            "cursor": "cursor",
            "grok": "grok",
            "kiro": "kiro",
            "chatgpt": "openai",
        }
        req.extra["luckmail_project_code"] = mapping.get(platform, platform)

    task_id = f"task_{int(time.time() * 1000)}"
    with _tasks_lock:
        _tasks[task_id] = {
            "id": task_id,
            "status": "pending",
            "progress": f"0/{req.count}",
            "logs": [],
        }
    background_tasks.add_task(_run_register, task_id, req)
    return {"task_id": task_id}


@router.get("/logs")
def get_logs(platform: str = None, page: int = 1, page_size: int = 50):
    with Session(engine) as s:
        q = select(TaskLog)
        if platform:
            q = q.where(TaskLog.platform == platform)
        q = q.order_by(TaskLog.id.desc())
        total = len(s.exec(q).all())
        items = s.exec(q.offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "items": items}


@router.post("/logs/batch-delete")
def batch_delete_logs(body: TaskLogBatchDeleteRequest):
    if not body.ids:
        raise HTTPException(400, "Task log IDs cannot be empty")
    unique_ids = list(dict.fromkeys(body.ids))
    if len(unique_ids) > 1000:
        raise HTTPException(400, "At most 1000 task logs per delete")

    with Session(engine) as s:
        try:
            logs = s.exec(select(TaskLog).where(TaskLog.id.in_(unique_ids))).all()
            found_ids = {log.id for log in logs if log.id is not None}
            for log in logs:
                s.delete(log)
            s.commit()
            return {
                "deleted": len(found_ids),
                "not_found": [log_id for log_id in unique_ids if log_id not in found_ids],
                "total_requested": len(unique_ids),
            }
        except Exception as e:
            s.rollback()
            raise HTTPException(500, f"Batch delete failed: {e}")


@router.get("/{task_id}/logs/stream")
async def stream_logs(task_id: str, since: int = 0):
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "Task not found")

    async def event_generator():
        sent = since
        while True:
            with _tasks_lock:
                logs = list(_tasks.get(task_id, {}).get("logs", []))
                status = _tasks.get(task_id, {}).get("status", "")
            while sent < len(logs):
                yield f"data: {json.dumps({'line': logs[sent]})}\n\n"
                sent += 1
            if status in ("done", "failed"):
                yield f"data: {json.dumps({'done': True, 'status': status})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/schedule/{task_id}/run")
def run_scheduled_task_now(task_id: str, background_tasks: BackgroundTasks):
    from core.scheduler import get_scheduled_register_tasks, update_task_run_status

    if is_worker_paused():
        state = get_worker_state()
        detail = state.get("reason") or "Workers are paused"
        raise HTTPException(409, f"Worker paused: {detail}")

    tasks = get_scheduled_register_tasks()
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    task_config = tasks[task_id]
    run_task_id = f"manual_{task_id}_{int(time.time())}"
    req = RegisterTaskRequest(**task_config)

    def run_with_status():
        try:
            with _tasks_lock:
                _tasks[run_task_id] = {
                    "id": run_task_id,
                    "status": "pending",
                    "progress": "0/1",
                    "logs": [],
                }
            _log(run_task_id, f"Start manual scheduled task {task_id}")
            _run_register(run_task_id, req)
            time.sleep(2)
            update_task_run_status(task_id, True, None)
        except Exception as e:
            import traceback

            update_task_run_status(task_id, False, f"{e}\n{traceback.format_exc()}")

    background_tasks.add_task(run_with_status)
    return {"task_id": run_task_id, "status": "running"}


@router.post("/schedule")
def create_scheduled_task(body: RegisterTaskRequest):
    import uuid
    from core.db import ScheduledTaskModel
    from core.scheduler import add_scheduled_register_task, update_task_run_status

    task_id = f"sched_{uuid.uuid4().hex[:8]}"
    safe_extra = _sanitize_task_payload(body.platform, body.extra)
    db_task = ScheduledTaskModel(
        task_id=task_id,
        platform=body.platform,
        count=body.count,
        executor_type=body.executor_type,
        captcha_solver=body.captcha_solver,
        extra_json=json.dumps(safe_extra, ensure_ascii=False),
        interval_type=body.interval_type or "minutes",
        interval_value=body.interval_value or 30,
        paused=False,
    )
    with Session(engine) as s:
        s.add(db_task)
        s.commit()
        s.refresh(db_task)

    config = _serialize_scheduled_task(db_task)
    add_scheduled_register_task(task_id, config)

    def run_now():
        run_task_id = f"scheduled_{task_id}_{int(time.time())}"
        success = False
        error_msg = None
        try:
            if is_worker_paused():
                state = get_worker_state()
                raise RuntimeError(f"Worker paused: {state.get('reason') or 'Workers are paused'}")
            with _tasks_lock:
                _tasks[run_task_id] = {
                    "id": run_task_id,
                    "status": "pending",
                    "progress": "0/1",
                    "logs": [],
                }
            req = RegisterTaskRequest(**config)
            _run_register(run_task_id, req)
            success = True
            print(f"[Scheduler] Task {task_id} executed", flush=True)
        except Exception as e:
            error_msg = str(e)
            print(f"[Scheduler] Task {task_id} failed: {e}", flush=True)
        finally:
            update_task_run_status(task_id, success, error_msg)

    threading.Thread(target=run_now, daemon=True).start()
    print(f"[Scheduler] Task {task_id} created and started", flush=True)
    return {"task_id": task_id, "status": "scheduled", "config": config}


@router.get("/schedule")
def list_scheduled_tasks():
    from core.db import ScheduledTaskModel
    from core.scheduler import get_all_task_run_status

    run_status = get_all_task_run_status()
    result = []
    with Session(engine) as s:
        tasks = s.exec(select(ScheduledTaskModel).order_by(ScheduledTaskModel.created_at.desc())).all()
    for task in tasks:
        task_data = _serialize_scheduled_task(task)
        task_id = task.task_id
        if task_id in run_status:
            task_data.update(run_status[task_id])
        else:
            task_data.setdefault("last_run_at", None)
            task_data.setdefault("last_run_success", None)
            task_data.setdefault("last_error", None)
        result.append(task_data)
    return {"tasks": result}


@router.put("/schedule")
def update_scheduled_task(body: RegisterTaskRequest):
    from core.db import ScheduledTaskModel
    from core.scheduler import add_scheduled_register_task, get_scheduled_register_tasks, remove_scheduled_register_task

    task_id = getattr(body, "task_id", None) or (body.extra and body.extra.get("task_id"))
    if not task_id:
        raise HTTPException(400, "Missing task ID")

    tasks = get_scheduled_register_tasks()
    if task_id not in tasks:
        with Session(engine) as s:
            existing = s.get(ScheduledTaskModel, task_id)
            if not existing:
                raise HTTPException(404, "Task not found")

    safe_extra = _sanitize_task_payload(body.platform, body.extra)
    with Session(engine) as s:
        task = s.get(ScheduledTaskModel, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        task.platform = body.platform
        task.count = body.count
        task.executor_type = body.executor_type
        task.captcha_solver = body.captcha_solver
        task.extra_json = json.dumps(safe_extra, ensure_ascii=False)
        task.interval_type = body.interval_type or "minutes"
        task.interval_value = body.interval_value or 30
        task.updated_at = datetime.now(timezone.utc)
        s.add(task)
        s.commit()
        s.refresh(task)
        config = _serialize_scheduled_task(task)

    if config.get("paused"):
        remove_scheduled_register_task(task_id)
    else:
        add_scheduled_register_task(task_id, config)

    return {"task_id": task_id, "status": "updated", "config": config}


@router.delete("/schedule/{task_id}")
def delete_scheduled_task(task_id: str):
    from core.db import ScheduledTaskModel
    from core.scheduler import remove_scheduled_register_task

    with Session(engine) as s:
        task = s.get(ScheduledTaskModel, task_id)
        if task:
            s.delete(task)
            s.commit()
    remove_scheduled_register_task(task_id)
    return {"ok": True}


@router.post("/schedule/{task_id}/toggle")
def toggle_scheduled_task(task_id: str):
    from core.db import ScheduledTaskModel
    from core.scheduler import add_scheduled_register_task, remove_scheduled_register_task

    with Session(engine) as s:
        task = s.get(ScheduledTaskModel, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        task.paused = not task.paused
        task.updated_at = datetime.now(timezone.utc)
        s.add(task)
        s.commit()
        s.refresh(task)
        config = _serialize_scheduled_task(task)

    if config.get("paused"):
        remove_scheduled_register_task(task_id)
    else:
        add_scheduled_register_task(task_id, config)

    return {"task_id": task_id, "paused": config["paused"]}


@router.get("/workers")
def get_active_workers():
    from core.db import get_in_use_parents
    from core.scheduler import get_running_scheduled_tasks

    return {
        "in_use_parents": get_in_use_parents(),
        "running_scheduled": get_running_scheduled_tasks(),
        "runtime": get_runtime_task_snapshot(),
    }


@router.get("/{task_id}")
def get_task(task_id: str):
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "Task not found")
        return _tasks[task_id]


@router.get("")
def list_tasks():
    with _tasks_lock:
        return list(_tasks.values())
