from datetime import datetime, timezone
import threading
import time

from sqlmodel import Session, select

from .base_platform import AccountStatus
from .config_store import config_store
from .db import AccountModel, ScheduledTaskModel, engine, ensure_schema
from services.worker_control import is_worker_paused


_scheduled_register_tasks = {}
_scheduled_tasks_lock = threading.Lock()

_task_run_status = {}
_task_status_lock = threading.Lock()

_running_tasks: dict[str, str] = {}  # task_id -> run_task_id
_running_tasks_lock = threading.Lock()


def _serialize_task(task: ScheduledTaskModel) -> dict:
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


def _sanitize_task(task: ScheduledTaskModel) -> bool:
    extra = task.get_extra()
    dirty = False
    if str(extra.get("network_mode", "")).strip().lower() not in {"direct", "proxy"}:
        extra["network_mode"] = "proxy"
        task.set_extra(extra)
        task.updated_at = datetime.now(timezone.utc)
        dirty = True
    if task.platform == "fotor" and extra.get("mail_provider") == "tempmail_lol":
        fallback_provider = str(config_store.get("mail_provider", "duckmail") or "duckmail").strip().lower()
        if fallback_provider == "tempmail_lol":
            fallback_provider = "duckmail"
        extra["mail_provider"] = fallback_provider
        task.set_extra(extra)
        task.updated_at = datetime.now(timezone.utc)
        dirty = True
        print(f"[Scheduler] sanitized legacy tempmail_lol task {task.task_id} -> {fallback_provider}")
    return dirty


class Scheduler:
    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._load_tasks_from_db()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[Scheduler] started, loaded {len(_scheduled_register_tasks)} tasks")

    def stop(self):
        self._running = False

    def _load_tasks_from_db(self):
        try:
            ensure_schema()
            with Session(engine) as s:
                tasks = s.exec(select(ScheduledTaskModel).where(ScheduledTaskModel.paused == False)).all()
                dirty = False
                for task in tasks:
                    if _sanitize_task(task):
                        s.add(task)
                        dirty = True
                    config = _serialize_task(task)
                    _scheduled_register_tasks[task.task_id] = config
                    print(f"[Scheduler] loaded task {task.task_id}")
                if dirty:
                    s.commit()
        except Exception as e:
            print(f"[Scheduler] failed to load tasks: {e}")

    def _loop(self):
        print("[Scheduler] waiting 5s before first scan...")
        time.sleep(5)
        while self._running:
            try:
                self.check_trial_expiry()
                self.check_and_run_scheduled_tasks()
            except Exception as e:
                print(f"[Scheduler] error: {e}")
            time.sleep(60)

    def check_trial_expiry(self):
        ensure_schema()
        now = int(datetime.now(timezone.utc).timestamp())
        with Session(engine) as s:
            accounts = s.exec(select(AccountModel).where(AccountModel.status == "trial")).all()
            updated = 0
            for acc in accounts:
                if acc.trial_end_time and acc.trial_end_time < now:
                    acc.status = AccountStatus.EXPIRED.value
                    acc.updated_at = datetime.now(timezone.utc)
                    s.add(acc)
                    updated += 1
            s.commit()
            if updated:
                print(f"[Scheduler] expired {updated} trial accounts")

    def check_and_run_scheduled_tasks(self):
        from api.tasks import RegisterTaskRequest, _run_register, _tasks, _tasks_lock

        if is_worker_paused():
            return

        with _scheduled_tasks_lock:
            tasks = dict(_scheduled_register_tasks)
        now = datetime.now(timezone.utc)

        for task_id, task_config in tasks.items():
            if task_config.get("paused", False):
                continue

            with _task_status_lock:
                run_status = _task_run_status.get(task_id)
            last_run_at = None
            if run_status and run_status.get("last_run_at"):
                try:
                    last_run_at = datetime.fromisoformat(run_status["last_run_at"])
                except Exception:
                    last_run_at = None

            interval_type = task_config.get("interval_type", "minutes")
            interval_value = int(task_config.get("interval_value", 30) or 30)
            interval_minutes = interval_value * 60 if interval_type == "hours" else interval_value

            should_run = last_run_at is None
            if last_run_at is not None:
                elapsed = (now - last_run_at).total_seconds() / 60
                should_run = elapsed >= interval_minutes

            if not should_run:
                continue

            # Overlap guard: skip if previous instance still running
            with _running_tasks_lock:
                if task_id in _running_tasks:
                    print(f"[Scheduler] SKIP {task_id} — previous instance {_running_tasks[task_id]} still running")
                    continue

            print(f"[Scheduler] executing scheduled task {task_id}")
            run_task_id = f"scheduled_{task_id}_{int(time.time())}"

            with _running_tasks_lock:
                _running_tasks[task_id] = run_task_id

            def run_task(task_id=task_id, task_config=task_config, run_task_id=run_task_id):
                try:
                    with _tasks_lock:
                        _tasks[run_task_id] = {
                            "id": run_task_id,
                            "status": "pending",
                            "progress": "0/1",
                            "logs": [],
                        }
                    req = RegisterTaskRequest(**task_config)
                    _run_register(run_task_id, req)
                    print(f"[Scheduler] task {task_id} completed")
                    update_task_run_status(task_id, True, None)
                except Exception as e:
                    print(f"[Scheduler] task {task_id} failed: {e}")
                    update_task_run_status(task_id, False, str(e))
                finally:
                    with _running_tasks_lock:
                        _running_tasks.pop(task_id, None)

            threading.Thread(target=run_task, daemon=True).start()


scheduler = Scheduler()


def add_scheduled_register_task(task_id: str, config: dict):
    with _scheduled_tasks_lock:
        _scheduled_register_tasks[task_id] = config


def remove_scheduled_register_task(task_id: str):
    with _scheduled_tasks_lock:
        if task_id in _scheduled_register_tasks:
            del _scheduled_register_tasks[task_id]


def get_scheduled_register_tasks():
    with _scheduled_tasks_lock:
        return dict(_scheduled_register_tasks)


def update_task_run_status(task_id: str, success: bool, error: str = None):
    with _task_status_lock:
        _task_run_status[task_id] = {
            "last_run_at": datetime.now(timezone.utc).isoformat(),
            "last_run_success": success,
            "last_error": error,
        }


def get_task_run_status(task_id: str):
    with _task_status_lock:
        return _task_run_status.get(task_id)


def get_all_task_run_status():
    with _task_status_lock:
        return dict(_task_run_status)


def get_running_scheduled_tasks() -> dict[str, str]:
    """Return {task_id: run_task_id} for tasks currently executing."""
    with _running_tasks_lock:
        return dict(_running_tasks)
