from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gc
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

import httpx
import psutil
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func
from sqlmodel import Session, select

from api.tasks import get_runtime_task_snapshot
from core.db import AccountModel, ProxyModel, ScheduledTaskModel, TaskLog, engine, ensure_schema
from core.scheduler import get_all_task_run_status, get_running_scheduled_tasks, scheduler
from services.worker_control import get_worker_state, pause_workers, resume_workers


logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None
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


def _is_admin_callback(callback: CallbackQuery) -> bool:
    try:
        return str(callback.message.chat.id) == _get_admin_chat_id()
    except Exception:
        return False


def _read_int_file(path: str) -> int | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _get_container_memory_stats() -> dict[str, float]:
    cgroup_v2_limit = _read_int_file("/sys/fs/cgroup/memory.max")
    cgroup_v2_used = _read_int_file("/sys/fs/cgroup/memory.current")
    cgroup_v1_limit = _read_int_file("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    cgroup_v1_used = _read_int_file("/sys/fs/cgroup/memory/memory.usage_in_bytes")

    host_mem = psutil.virtual_memory()
    huge_limit = 1 << 60

    limit = cgroup_v2_limit or cgroup_v1_limit
    used = cgroup_v2_used or cgroup_v1_used

    if limit is None or limit <= 0 or limit >= huge_limit:
        limit = int(host_mem.total)
    if used is None or used < 0:
        used = int(host_mem.used)

    percent = (used / limit * 100.0) if limit > 0 else 0.0
    return {
        "used_bytes": float(used),
        "limit_bytes": float(limit),
        "percent": float(percent),
    }


def _worker_state_label(paused: bool) -> str:
    return "🔴 ĐANG NGHỈ (Paused)" if paused else "🟢 ĐANG CÀY (Running)"


def _home_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🏠 Menu Chính", callback_data="menu:home")


def _wrap_markup(rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows = rows or []
    rows.append([_home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Status", callback_data="menu:status")],
            [InlineKeyboardButton(text="⚙️ Quản lý Workers", callback_data="menu:workers")],
            [InlineKeyboardButton(text="📝 Xem Logs", callback_data="menu:logs")],
            [InlineKeyboardButton(text="🔄 Đổi Proxy", callback_data="menu:rotate_proxy")],
        ]
    )


async def _send_main_menu(chat_id: int) -> None:
    if not _bot:
        return
    await _bot.send_message(
        chat_id=chat_id,
        text="🏠 Menu Chính\nChọn chức năng bên dưới:",
        reply_markup=_main_menu_markup(),
    )


async def _safe_send(text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    if not _bot or not is_enabled():
        return
    try:
        await _bot.send_message(
            chat_id=int(_get_admin_chat_id()),
            text=text,
            reply_markup=reply_markup or _wrap_markup(),
        )
    except Exception:
        logger.exception("Failed to send Telegram message")


async def _reply_message(message: Message, text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    await message.answer(text, reply_markup=reply_markup or _wrap_markup())


def _collect_status_snapshot() -> dict[str, Any]:
    ensure_schema()
    with Session(engine) as session:
        account_total = session.exec(select(func.count()).select_from(AccountModel)).one()
        max_ref_accounts = session.exec(
            select(func.count())
            .select_from(AccountModel)
            .where(AccountModel.platform == "fotor")
            .where(AccountModel.referred_count >= 20)
        ).one()
        failed_count = session.exec(
            select(func.count()).select_from(TaskLog).where(TaskLog.status == "failed")
        ).one()

    runtime = get_runtime_task_snapshot()
    running_scheduled = get_running_scheduled_tasks()
    mem = _get_container_memory_stats()
    cpu = psutil.cpu_percent(interval=0.2)
    worker_state = get_worker_state()

    return {
        "account_total": int(account_total or 0),
        "max_ref_accounts": int(max_ref_accounts or 0),
        "failed_count": int(failed_count or 0),
        "cpu_percent": float(cpu),
        "ram_percent": float(mem["percent"]),
        "ram_used_gb": mem["used_bytes"] / (1024 ** 3),
        "ram_total_gb": mem["limit_bytes"] / (1024 ** 3),
        "worker_state": worker_state,
        "runtime": runtime,
        "running_scheduled": running_scheduled,
    }


def _build_status_message() -> str:
    snapshot = _collect_status_snapshot()
    pause_reason = snapshot["worker_state"].get("reason") or "-"
    return (
        "📊 AutoReg Fotor Status\n"
        f"- Acc đã Reg / Acc đã đủ Ref: {snapshot['account_total']} / {snapshot['max_ref_accounts']}\n"
        f"- Reg fail logs: {snapshot['failed_count']}\n"
        f"- CPU / RAM: {snapshot['cpu_percent']:.1f}% / {snapshot['ram_percent']:.1f}% "
        f"({snapshot['ram_used_gb']:.1f}GB / {snapshot['ram_total_gb']:.1f}GB)\n"
        f"- Worker: {_worker_state_label(bool(snapshot['worker_state'].get('paused')))}\n"
        f"- Active runtime tasks: {snapshot['runtime']['active']}\n"
        f"- Running scheduled jobs: {len(snapshot['running_scheduled'])}\n"
        f"- Pause reason: {pause_reason}\n"
        f"- Updated: {_now_str()}"
    )


def _get_fotor_scheduled_tasks() -> list[ScheduledTaskModel]:
    ensure_schema()
    with Session(engine) as session:
        return session.exec(
            select(ScheduledTaskModel)
            .where(ScheduledTaskModel.platform == "fotor")
            .order_by(ScheduledTaskModel.created_at.asc())
        ).all()


def _build_workers_keyboard(tasks: list[ScheduledTaskModel]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, task in enumerate(tasks, start=1):
        is_paused = bool(task.paused)
        label = f"{'Bật' if is_paused else 'Tắt'} Worker {idx}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"worker:toggle:{task.task_id}")])
    rows.append([InlineKeyboardButton(text="🔄 Đổi Proxy", callback_data="menu:rotate_proxy")])
    return _wrap_markup(rows)


def _build_workers_message() -> tuple[str, InlineKeyboardMarkup]:
    worker_state = get_worker_state()
    running_map = get_running_scheduled_tasks()
    run_status = get_all_task_run_status()
    runtime = get_runtime_task_snapshot()
    tasks = _get_fotor_scheduled_tasks()

    lines = [
        "⚙️ Quản lý Workers Fotor",
        f"- Worker tổng: {_worker_state_label(bool(worker_state.get('paused')))}",
        f"- Pause reason: {worker_state.get('reason') or '-'}",
        f"- Active runtime tasks: {runtime['active']}",
        f"- Running scheduled jobs: {len(running_map)}",
    ]
    if not tasks:
        lines.append("- Không có scheduled worker Fotor")
        return "\n".join(lines), _wrap_markup()

    lines.append("")
    for idx, task in enumerate(tasks, start=1):
        task_run = run_status.get(task.task_id, {})
        paused = bool(task.paused)
        is_running = task.task_id in running_map
        state = _worker_state_label(paused)
        if is_running:
            state += " | đang thực thi"
        last_ok = task_run.get("last_run_success")
        if last_ok is True:
            last_result = "ok"
        elif last_ok is False:
            last_result = "fail"
        else:
            last_result = "-"
        lines.append(
            f"- Worker {idx}: {state} | count={task.count} | every {task.interval_value} {task.interval_type} | last={last_result}"
        )
    return "\n".join(lines), _build_workers_keyboard(tasks)


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
        return "📝 Fotor Logs\n- No logs yet"

    lines = ["📝 Fotor Logs (latest 8)"]
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

    mem = _get_container_memory_stats()
    now = time.time()
    if mem["percent"] >= 90 and now - _last_ram_alert_ts >= 900:
        await _safe_send(
            f"⚠️ CẢNH BÁO RAM: RAM container đang ở mức {mem['percent']:.1f}% "
            f"({mem['used_bytes'] / (1024 ** 3):.1f}GB / {mem['limit_bytes'] / (1024 ** 3):.1f}GB)."
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


def _normalize_proxy_entry(item: Any) -> str | None:
    if isinstance(item, str):
        value = item.strip()
        return value or None
    if not isinstance(item, dict):
        return None

    for key in ("url", "proxy", "value"):
        raw = str(item.get(key, "") or "").strip()
        if raw:
            return raw

    ip = str(item.get("ip", "") or item.get("host", "") or "").strip()
    port = str(item.get("port", "") or "").strip()
    username = str(item.get("user", "") or item.get("username", "") or "").strip()
    password = str(item.get("pass", "") or item.get("password", "") or "").strip()
    if ip and port:
        if username or password:
            return f"{ip}:{port}:{username}:{password}"
        return f"{ip}:{port}"
    return None


async def _fetch_proxy_payload() -> list[str]:
    url = str(os.getenv("PROXY_API_URL", "")).strip()
    secret = str(os.getenv("PROXY_SECRET_KEY", "")).strip()
    if not url:
        raise RuntimeError("PROXY_API_URL is missing")
    if not secret:
        raise RuntimeError("PROXY_SECRET_KEY is missing")

    headers = {
        "Authorization": f"Bearer {secret}",
        "X-Proxy-Secret-Key": secret,
    }
    timeout = httpx.Timeout(180.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            detail = response.text.strip()
            if len(detail) > 240:
                detail = detail[:237] + "..."
            raise RuntimeError(
                f"Proxy API HTTP {response.status_code}"
                + (f" | {detail}" if detail else "")
            )
        payload = response.json()

    proxies_raw = []
    if isinstance(payload, dict):
        for key in ("proxies", "data", "items", "result"):
            if isinstance(payload.get(key), list):
                proxies_raw = payload[key]
                break
    elif isinstance(payload, list):
        proxies_raw = payload

    proxies: list[str] = []
    seen: set[str] = set()
    for item in proxies_raw:
        normalized = _normalize_proxy_entry(item)
        if normalized and normalized not in seen:
            proxies.append(normalized)
            seen.add(normalized)

    if not proxies:
        raise RuntimeError("Proxy API returned no usable proxies")
    return proxies


def _replace_proxy_inventory(proxies: list[str]) -> int:
    ensure_schema()
    with Session(engine) as session:
        existing = session.exec(select(ProxyModel)).all()
        existing_by_url = {proxy.url: proxy for proxy in existing}
        desired = set(proxies)

        for proxy in existing:
            if proxy.url not in desired:
                session.delete(proxy)

        added = 0
        for proxy_url in proxies:
            row = existing_by_url.get(proxy_url)
            if row:
                row.is_active = True
                session.add(row)
            else:
                session.add(ProxyModel(url=proxy_url, is_active=True))
                added += 1
        session.commit()
    return len(proxies)


async def _rotate_proxies_flow() -> tuple[bool, str]:
    pause_workers("Proxy rotation in progress")
    await _safe_send(
        "⏳ Đang Pause hệ thống và yêu cầu Xưởng ĐIỀU CHẾ Proxy mới "
        "(Quá trình này mất 1-2 phút do phải giải Captcha, xin giữ máy)..."
    )
    try:
        proxies = await _fetch_proxy_payload()
        total = _replace_proxy_inventory(proxies)
    except Exception as e:
        logger.exception("Proxy rotation failed")
        return False, f"❌ Lỗi lấy Proxy, hệ thống vẫn đang Pause.\nChi tiết: {e}"

    resume_workers()
    return True, f"✅ Đã nạp thành công {total} Proxy mới từ Xưởng. Hệ thống đang Auto-Resume..."


async def _toggle_worker_task(task_id: str) -> dict[str, Any]:
    from api.tasks import toggle_scheduled_task

    result = toggle_scheduled_task(task_id)
    return result


def _register_handlers() -> None:
    global _dp

    _dp = Dispatcher()
    router = Router()

    @router.message(Command("status"))
    async def status_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _build_status_message())

    @router.message(Command("pause"))
    async def pause_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        state = pause_workers("Paused from Telegram")
        await _reply_message(
            message,
            "⏸ Worker paused. Jobs đang chạy sẽ finish nốt rồi nghỉ.\n"
            f"Trạng thái: {_worker_state_label(True)}\n"
            f"Reason: {state.get('reason') or '-'}",
        )

    @router.message(Command("resume"))
    async def resume_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        resume_workers()
        await _reply_message(
            message,
            "▶️ Worker resumed.\n"
            f"Trạng thái: {_worker_state_label(False)}",
        )

    @router.message(Command("restart"))
    async def restart_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, "♻️ Restart command accepted. Starting graceful restart...")
        asyncio.create_task(_graceful_restart())

    @router.message(Command("workers"))
    async def workers_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        text, markup = _build_workers_message()
        await _reply_message(message, text, reply_markup=markup)

    @router.message(Command("logs"))
    async def logs_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _build_logs_message())

    @router.callback_query(lambda c: c.data == "menu:home")
    async def home_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer()
        await _send_main_menu(callback.message.chat.id)

    @router.callback_query(lambda c: c.data == "menu:status")
    async def status_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer()
        await callback.message.answer(_build_status_message(), reply_markup=_wrap_markup())

    @router.callback_query(lambda c: c.data == "menu:workers")
    async def workers_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer()
        text, markup = _build_workers_message()
        await callback.message.answer(text, reply_markup=markup)

    @router.callback_query(lambda c: c.data == "menu:logs")
    async def logs_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer()
        await callback.message.answer(_build_logs_message(), reply_markup=_wrap_markup())

    @router.callback_query(lambda c: c.data == "menu:rotate_proxy")
    async def rotate_proxy_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đang đổi proxy...")
        ok, message = await _rotate_proxies_flow()
        await callback.message.answer(message, reply_markup=_wrap_markup())
        if ok:
            text, markup = _build_workers_message()
            await callback.message.answer(text, reply_markup=markup)

    @router.callback_query(lambda c: bool(c.data and c.data.startswith("worker:toggle:")))
    async def toggle_worker_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        task_id = callback.data.split(":", 2)[2]
        result = await _toggle_worker_task(task_id)
        paused = bool(result.get("paused"))
        await callback.answer("Đã cập nhật worker")
        text, markup = _build_workers_message()
        await callback.message.answer(
            f"✅ Worker `{task_id}` chuyển sang trạng thái: {_worker_state_label(paused)}",
            reply_markup=_wrap_markup(),
            parse_mode="Markdown",
        )
        await callback.message.answer(text, reply_markup=markup)

    _dp.include_router(router)


async def start_telegram_bot() -> None:
    global _bot, _polling_task, _monitor_task, _last_seen_task_log_id

    if not is_enabled():
        logger.info("Telegram bot disabled: TELEGRAM_BOT_TOKEN or ADMIN_CHAT_ID missing")
        return

    if _polling_task and not _polling_task.done():
        return

    _register_handlers()
    _bot = Bot(token=_get_bot_token())
    try:
        await _bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        logger.exception("Failed to delete Telegram webhook before polling")

    try:
        await _bot.set_my_commands(
            [
                BotCommand(command="status", description="Xem trạng thái hệ thống"),
                BotCommand(command="workers", description="Quản lý worker Fotor"),
                BotCommand(command="logs", description="Xem log Fotor gần nhất"),
                BotCommand(command="pause", description="Tạm dừng worker"),
                BotCommand(command="resume", description="Bật lại worker"),
                BotCommand(command="restart", description="Restart dịch vụ"),
            ]
        )
    except Exception:
        logger.exception("Failed to set Telegram bot commands")

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
