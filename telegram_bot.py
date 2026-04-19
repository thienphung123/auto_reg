from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gc
import logging
import os
import sys
import time
from typing import Any

import psutil
from sqlalchemy import func
from sqlmodel import Session, select

from api.tasks import get_runtime_task_snapshot
from core.db import AccountModel, ScheduledTaskModel, TaskLog, engine, ensure_schema
from core.scheduler import (
    get_all_task_run_status,
    get_running_scheduled_tasks,
    scheduler,
)
from services.worker_control import get_worker_state, pause_workers, resume_workers


logger = logging.getLogger(__name__)

_bot = None
_dp = None
_polling_task: asyncio.Task | None = None
_monitor_task: asyncio.Task | None = None
_stop_event = asyncio.Event()

_last_seen_task_log_id = 0
_consecutive_failures = 0
_failure_alert_sent = False
_last_ram_alert_ts = 0.0


def _get_admin_chat_id() -> str:
    return str(os.getenv("ADMIN_CHAT_ID", "")).strip()


def _get_bot_token() -> str:
    return str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()


def is_enabled() -> bool:
    return bool(_get_bot_token() and _get_admin_chat_id())


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _is_admin_chat(message: Any) -> bool:
    try:
        return str(message.chat.id) == _get_admin_chat_id()
    except Exception:
        return False


def _collect_status_snapshot() -> dict[str, Any]:
    ensure_schema()
    with Session(engine) as session:
        success_count = session.exec(
            select(func.count()).select_from(TaskLog).where(TaskLog.status == "success")
        ).one()
        failed_count = session.exec(
            select(func.count()).select_from(TaskLog).where(TaskLog.status == "failed")
        ).one()
        account_total = session.exec(select(func.count()).select_from(AccountModel)).one()

    runtime = get_runtime_task_snapshot()
    running_scheduled = get_running_scheduled_tasks()
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.2)
    worker_state = get_worker_state()

    if worker_state.get("paused"):
        worker_label = "Paused"
    elif runtime["active"] > 0 or running_scheduled:
        worker_label = "Đang cày"
    else:
        worker_label = "Đang nghỉ"

    return {
        "success_count": int(success_count or 0),
        "failed_count": int(failed_count or 0),
        "account_total": int(account_total or 0),
        "cpu_percent": float(cpu),
        "ram_percent": float(mem.percent),
        "ram_used_gb": mem.used / (1024 ** 3),
        "ram_total_gb": mem.total / (1024 ** 3),
        "worker_label": worker_label,
        "worker_state": worker_state,
        "runtime": runtime,
        "running_scheduled": running_scheduled,
    }


def _build_status_message() -> str:
    snapshot = _collect_status_snapshot()
    pause_reason = snapshot["worker_state"].get("reason") or "-"
    return (
        "📊 AutoReg Fotor Status\n"
        f"- Success / Failed: {snapshot['success_count']} / {snapshot['failed_count']}\n"
        f"- Total Accounts: {snapshot['account_total']}\n"
        f"- CPU / RAM: {snapshot['cpu_percent']:.1f}% / {snapshot['ram_percent']:.1f}% "
        f"({snapshot['ram_used_gb']:.1f}GB / {snapshot['ram_total_gb']:.1f}GB)\n"
        f"- Worker: {snapshot['worker_label']}\n"
        f"- Active Runtime Tasks: {snapshot['runtime']['active']}\n"
        f"- Running Scheduled Jobs: {len(snapshot['running_scheduled'])}\n"
        f"- Pause Reason: {pause_reason}\n"
        f"- Updated: {_now_str()}"
    )


async def _safe_send(text: str) -> None:
    if not _bot or not is_enabled():
        return
    try:
        await _bot.send_message(chat_id=int(_get_admin_chat_id()), text=text)
    except Exception:
        logger.exception("Failed to send Telegram message")


def _build_workers_message() -> str:
    ensure_schema()
    worker_state = get_worker_state()
    running_map = get_running_scheduled_tasks()
    run_status = get_all_task_run_status()
    runtime = get_runtime_task_snapshot()

    with Session(engine) as session:
        scheduled = session.exec(
            select(ScheduledTaskModel)
            .where(ScheduledTaskModel.platform == "fotor")
            .order_by(ScheduledTaskModel.created_at.desc())
        ).all()

    lines = [
        "🛠 Fotor Workers",
        f"- Worker paused: {'yes' if worker_state.get('paused') else 'no'}",
        f"- Pause reason: {worker_state.get('reason') or '-'}",
        f"- Active runtime tasks: {runtime['active']}",
        f"- Running scheduled jobs: {len(running_map)}",
    ]
    if not scheduled:
        lines.append("- No scheduled Fotor task")
        return "\n".join(lines)

    lines.append("")
    for task in scheduled[:10]:
        task_run = run_status.get(task.task_id, {})
        is_running = task.task_id in running_map
        paused = bool(task.paused)
        state = "running" if is_running else "paused" if paused else "idle"
        last_ok = task_run.get("last_run_success")
        if last_ok is True:
            last_result = "ok"
        elif last_ok is False:
            last_result = "fail"
        else:
            last_result = "-"
        err = str(task_run.get("last_error") or "").strip().replace("\n", " ")
        if len(err) > 80:
            err = err[:77] + "..."
        lines.append(
            f"- {task.task_id}: {state} | count={task.count} | every {task.interval_value} {task.interval_type} | last={last_result}"
        )
        if err:
            lines.append(f"  err: {err}")
    return "\n".join(lines)


def _build_logs_message() -> str:
    ensure_schema()
    with Session(engine) as session:
        logs = session.exec(
            select(TaskLog)
            .where(TaskLog.platform == "fotor")
            .order_by(TaskLog.id.desc())
            .limit(8)
        ).all()

    if not logs:
        return "📜 Fotor Logs\n- No logs yet"

    lines = ["📜 Fotor Logs (latest 8)"]
    for log in logs:
        status = str(log.status or "-")
        email = str(log.email or "-")
        created = log.created_at.strftime("%m-%d %H:%M") if log.created_at else "-"
        err = str(log.error or "").strip().replace("\n", " ")
        if len(err) > 90:
            err = err[:87] + "..."
        lines.append(f"- [{created}] {status} | {email}")
        if err:
            lines.append(f"  err: {err}")
    return "\n".join(lines)


def _get_latest_task_log_id() -> int:
    ensure_schema()
    with Session(engine) as session:
        latest = session.exec(select(TaskLog.id).order_by(TaskLog.id.desc())).first()
        return int(latest or 0)


def _get_new_task_logs(last_seen_id: int) -> list[TaskLog]:
    ensure_schema()
    with Session(engine) as session:
        return session.exec(
            select(TaskLog).where(TaskLog.id > last_seen_id).order_by(TaskLog.id.asc())
        ).all()


async def _monitor_failures() -> None:
    global _last_seen_task_log_id, _consecutive_failures, _failure_alert_sent

    logs = _get_new_task_logs(_last_seen_task_log_id)
    if not logs:
        return

    for log in logs:
        _last_seen_task_log_id = max(_last_seen_task_log_id, int(log.id or 0))
        status = str(log.status or "").lower()
        if status == "success":
            _consecutive_failures = 0
            _failure_alert_sent = False
        elif status == "failed":
            _consecutive_failures += 1
            if _consecutive_failures >= 3 and not _failure_alert_sent:
                pause_workers("3 consecutive registration failures")
                await _safe_send(
                    "⚠️ BÁO ĐỘNG: Lỗi reg xịt 3 acc liên tục! "
                    "Hệ thống đã tự động Pause Worker để bảo toàn lực lượng, sếp vào check ngay!"
                )
                _failure_alert_sent = True


async def _monitor_ram() -> None:
    global _last_ram_alert_ts

    mem = psutil.virtual_memory()
    now = time.time()
    if mem.percent >= 90 and now - _last_ram_alert_ts >= 900:
        await _safe_send(
            f"⚠️ CẢNH BÁO RAM: RAM đang ở mức {mem.percent:.1f}% "
            f"({mem.used / (1024 ** 3):.1f}GB / {mem.total / (1024 ** 3):.1f}GB)."
        )
        _last_ram_alert_ts = now


async def _monitor_loop() -> None:
    while not _stop_event.is_set():
        try:
            await _monitor_failures()
            await _monitor_ram()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram monitor loop failed")
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass


async def _graceful_restart() -> None:
    pause_workers("Telegram /restart requested")
    await _safe_send("♻️ Restart requested. Pausing workers and waiting for running jobs to finish...")

    deadline = time.time() + 180
    while time.time() < deadline:
        runtime = get_runtime_task_snapshot()
        if runtime["active"] == 0 and not get_running_scheduled_tasks():
            break
        await asyncio.sleep(5)

    try:
        scheduler.stop()
    except Exception:
        logger.exception("Failed to stop scheduler during restart")

    try:
        from services.solver_manager import stop as stop_solver

        stop_solver()
    except Exception:
        logger.exception("Failed to stop solver during restart")

    gc.collect()
    await _safe_send("♻️ Restarting FastAPI service now.")
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable, *sys.argv])


def _register_handlers() -> None:
    global _dp

    from aiogram import Dispatcher, Router
    from aiogram.filters import Command
    from aiogram.types import Message

    _dp = Dispatcher()
    router = Router()

    @router.message(Command("status"))
    async def status_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await message.answer(_build_status_message())

    @router.message(Command("pause"))
    async def pause_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        state = pause_workers("Paused from Telegram")
        await message.answer(
            "⏸ Worker paused. Jobs đang chạy sẽ finish nốt rồi nghỉ.\n"
            f"Reason: {state.get('reason') or '-'}"
        )

    @router.message(Command("resume"))
    async def resume_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        resume_workers()
        await message.answer("▶️ Worker resumed.")

    @router.message(Command("restart"))
    async def restart_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await message.answer("♻️ Restart command accepted. Starting graceful restart...")
        asyncio.create_task(_graceful_restart())

    @router.message(Command("workers"))
    async def workers_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await message.answer(_build_workers_message())

    @router.message(Command("logs"))
    async def logs_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await message.answer(_build_logs_message())

    _dp.include_router(router)


async def start_telegram_bot() -> None:
    global _bot, _polling_task, _monitor_task, _last_seen_task_log_id

    if not is_enabled():
        logger.info("Telegram bot disabled: TELEGRAM_BOT_TOKEN or ADMIN_CHAT_ID missing")
        return

    if _polling_task and not _polling_task.done():
        return

    try:
        from aiogram import Bot
    except ImportError:
        logger.exception("aiogram is not installed; Telegram bot disabled")
        return

    _register_handlers()
    _bot = Bot(token=_get_bot_token())
    try:
        await _bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        logger.exception("Failed to delete Telegram webhook before polling")
    _stop_event.clear()
    _last_seen_task_log_id = _get_latest_task_log_id()
    _polling_task = asyncio.create_task(_dp.start_polling(_bot))
    _monitor_task = asyncio.create_task(_monitor_loop())
    logger.info("Telegram bot started")
    await _safe_send("🤖 Telegram bot connected. Remote dashboard is online.")


async def stop_telegram_bot() -> None:
    global _bot, _dp, _polling_task, _monitor_task

    _stop_event.set()
    tasks = [task for task in (_monitor_task, _polling_task) if task]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    if _bot is not None:
        try:
            await _bot.session.close()
        except Exception:
            logger.exception("Failed to close Telegram bot session")

    _bot = None
    _dp = None
    _polling_task = None
    _monitor_task = None
    logger.info("Telegram bot stopped")
