from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gc
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

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
from openai import AsyncOpenAI
from sqlalchemy import func
from sqlmodel import Session, select

from api.tasks import get_runtime_task_snapshot
from core.config_store import config_store
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
_last_health_alert_key = ""
_last_health_alert_ts = 0.0
_last_daily_summary_date = ""
_AI_COMMAND_PATTERN = re.compile(r"\[(CMD_[A-Z0-9_]+)\]")


def _get_admin_chat_id() -> str:
    return str(os.getenv("ADMIN_CHAT_ID", "")).strip()


def _get_bot_token() -> str:
    return str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()


def _get_hf_api_token() -> str:
    return str(os.getenv("HF_API_TOKEN", "")).strip()


def _get_ai_api_url() -> str:
    return str(os.getenv("AI_API_URL", "https://phungai.eu.cc/v1")).strip() or "https://phungai.eu.cc/v1"


def _get_ai_api_key() -> str:
    return str(os.getenv("AI_API_KEY", "")).strip()


def _get_ai_model_id() -> str:
    return str(os.getenv("AI_MODEL_ID", "")).strip()


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


def _get_max_failures_threshold() -> int:
    raw = str(
        os.getenv("MAX_FAILS", "")
        or config_store.get("max_fails", "")
        or config_store.get("max_failures", "")
        or "3"
    ).strip()
    try:
        value = int(raw)
    except Exception:
        value = 3
    return max(1, value)


def get_village_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Mùa vụ", callback_data="cmd_status"),
                InlineKeyboardButton(text="📈 Thống kê", callback_data="cmd_summary"),
            ],
            [
                InlineKeyboardButton(text="⛺ Nghỉ", callback_data="cmd_pause"),
                InlineKeyboardButton(text="🚜 Ra đồng", callback_data="cmd_resume"),
            ],
            [InlineKeyboardButton(text="💧 Đổi mương", callback_data="cmd_changeproxy")],
        ]
    )


def get_worker_menu() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, task in enumerate(_get_fotor_scheduled_tasks(), start=1):
        if bool(task.paused):
            rows.append(
                [InlineKeyboardButton(text=f"🚜 Bật Worker {index}", callback_data=f"cmd_resume_worker_{index}")]
            )
        else:
            rows.append(
                [InlineKeyboardButton(text=f"⛺ Tắt Worker {index}", callback_data=f"cmd_pause_worker_{index}")]
            )
    if not rows:
        rows.append([InlineKeyboardButton(text="📊 Mùa vụ", callback_data="cmd_status")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _compact_village_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Mùa vụ", callback_data="cmd_status"),
                InlineKeyboardButton(text="📈 Thống kê", callback_data="cmd_summary"),
            ],
            [
                InlineKeyboardButton(text="⛺ Nghỉ", callback_data="cmd_pause"),
                InlineKeyboardButton(text="🚜 Ra đồng", callback_data="cmd_resume"),
            ],
        ]
    )


def _select_menu_for_text(text: str | None = None) -> InlineKeyboardMarkup:
    content = str(text or "")
    if len(content) > 900 or content.count("\n") > 16:
        return _compact_village_menu()
    return get_village_menu()


async def _safe_send(
    text: str,
    *,
    with_menu: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if not _bot or not is_enabled():
        return
    try:
        await _bot.send_message(
            chat_id=int(_get_admin_chat_id()),
            text=text,
            reply_markup=reply_markup if reply_markup is not None else (_select_menu_for_text(text) if with_menu else None),
        )
    except Exception:
        logger.exception("Failed to send Telegram message")


async def _reply_message(
    message: Message,
    text: str,
    *,
    with_menu: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    await message.answer(
        text,
        reply_markup=reply_markup if reply_markup is not None else (_select_menu_for_text(text) if with_menu else None),
    )


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
        return "\n".join(lines), get_worker_menu()

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
    return "\n".join(lines), get_worker_menu()


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


def _get_recent_log_lines(limit: int = 8) -> list[str]:
    ensure_schema()
    with Session(engine) as session:
        logs = session.exec(
            select(TaskLog)
            .where(TaskLog.platform == "fotor")
            .order_by(TaskLog.id.desc())
            .limit(limit)
        ).all()

    lines: list[str] = []
    for log in reversed(logs):
        created = log.created_at.strftime("%m-%d %H:%M:%S") if log.created_at else "-"
        status = str(log.status or "-")
        email = str(log.email or "-")
        err = str(log.error or "").strip().replace("\n", " ")
        if len(err) > 160:
            err = err[:157] + "..."
        line = f"[{created}] {status} | {email}"
        if err:
            line += f" | err={err}"
        lines.append(line)
    return lines


def _get_worker_switch_activity(worker_state: dict[str, Any], runtime: dict[str, Any]) -> tuple[str, str]:
    switch_state = "DISABLED" if bool(worker_state.get("paused")) else "ENABLED"
    activity_state = "RUNNING" if int(runtime.get("active") or 0) > 0 else "IDLE"
    return switch_state, activity_state


def _infer_worker_network_mode(active_proxies: int) -> str:
    return "Dùng Proxy" if active_proxies > 0 else "Chạy Direct Server"


def _get_worker_snapshots() -> list[dict[str, Any]]:
    tasks = _get_fotor_scheduled_tasks()
    running_map = get_running_scheduled_tasks()
    run_status = get_all_task_run_status()
    runtime = get_runtime_task_snapshot()
    active_proxies = 0
    with Session(engine) as session:
        active_proxies = int(
            session.exec(
                select(func.count()).select_from(ProxyModel).where(ProxyModel.is_active == True)
            ).one()
            or 0
        )

    switch_state, activity_state = _get_worker_switch_activity(get_worker_state(), runtime)
    network_mode = _infer_worker_network_mode(active_proxies)
    snapshots: list[dict[str, Any]] = []

    for idx, task in enumerate(tasks, start=1):
        extra = task.get_extra()
        mail_provider = str(extra.get("mail_provider") or config_store.get("mail_provider", "duckmail") or "duckmail").strip()
        if task.paused:
            state_label = "Đang nghỉ"
        elif task.task_id in running_map:
            state_label = "Đang cày"
        elif switch_state == "ENABLED" and activity_state == "IDLE":
            state_label = "Đang trực chiến"
        else:
            state_label = "Đang chờ lịch"
        snapshots.append(
            {
                "index": idx,
                "task_id": task.task_id,
                "paused": bool(task.paused),
                "status_label": state_label,
                "mail_provider": mail_provider,
                "network_mode": network_mode,
                "last_run_success": run_status.get(task.task_id, {}).get("last_run_success"),
            }
        )
    return snapshots


def get_live_system_context() -> str:
    snapshot = _collect_status_snapshot()
    runtime = snapshot["runtime"]
    worker_state = snapshot["worker_state"]
    tasks = _get_fotor_scheduled_tasks()
    active_proxies = 0
    total_proxies = 0
    with Session(engine) as session:
        total_proxies = int(session.exec(select(func.count()).select_from(ProxyModel)).one() or 0)
        active_proxies = int(
            session.exec(
                select(func.count()).select_from(ProxyModel).where(ProxyModel.is_active == True)
            ).one()
            or 0
        )

    config_values = config_store.get_all()
    worker_total = len(tasks)
    worker_counts = [str(task.count) for task in tasks[:6]]
    recent_logs = _get_recent_log_lines(limit=8)
    switch_state, activity_state = _get_worker_switch_activity(worker_state, runtime)
    worker_snapshots = _get_worker_snapshots()

    config_lines = [
        f"- MAX_FAILS hiện tại: {_get_max_failures_threshold()}",
        f"- Tổng worker Fotor đã lên lịch: {worker_total}",
        f"- Count mỗi worker: {', '.join(worker_counts) if worker_counts else '-'}",
        f"- Worker Switch (mở cổng nhận việc): {switch_state}",
        f"- Worker Activity (thực tế ngoài đồng): {activity_state}",
        f"- Pause reason: {worker_state.get('reason') or '-'}",
        f"- Mail provider: {config_values.get('mail_provider', '-')}",
        f"- Default executor: {config_values.get('default_executor', '-')}",
        f"- Captcha solver: {config_values.get('default_captcha_solver', '-')}",
        f"- AI model đang cấu hình: {_get_ai_model_id() or '-'}",
    ]

    stats_lines = [
        f"- Acc đã reg: {snapshot['account_total']}",
        f"- Acc đủ ref: {snapshot['max_ref_accounts']}",
        f"- Failed logs: {snapshot['failed_count']}",
        f"- Proxy sống / tổng proxy: {active_proxies} / {total_proxies}",
        f"- Active Runtime Tasks (số máy đang thực sự cày): {runtime['active']}",
        f"- Runtime counts: pending={runtime['counts']['pending']}, running={runtime['counts']['running']}, done={runtime['counts']['done']}, failed={runtime['counts']['failed']}",
        f"- Scheduled Jobs (số máy nằm chờ trong hàng đợi): {len(snapshot['running_scheduled'])}",
        "- Lưu ý cho AI: Nếu Active Runtime Tasks = 0 nghĩa là KHÔNG CÓ AI ĐANG CÀY, dù Scheduled Jobs có lớn hơn 0 đi nữa.",
        "- Lưu ý cho AI: Nếu Switch là ENABLED nhưng Activity là IDLE thì báo là: Anh em đang ngồi trực chiến đợi giờ đẹp sếp ạ.",
        f"- CPU/RAM: {snapshot['cpu_percent']:.1f}% / {snapshot['ram_percent']:.1f}%",
        f"- Consecutive fails đang ghi nhận: {_consecutive_failures}",
        f"- Cập nhật lúc: {_now_str()}",
    ]

    worker_lines = []
    for item in worker_snapshots:
        worker_lines.append(
            f"- Worker {item['index']}: [Trạng thái: {item['status_label']}], "
            f"[Mail Provider đang dùng: {item['mail_provider']}], "
            f"[Kiểu mạng: {item['network_mode']}]"
        )
    if not worker_lines:
        worker_lines.append("- Chưa có worker nào trong hệ thống.")

    log_lines = recent_logs or ["- Chưa có log mới."]
    return (
        "[CONFIG HIỆN TẠI]\n"
        + "\n".join(config_lines)
        + "\n\n[THỐNG KÊ CƠ BẢN]\n"
        + "\n".join(stats_lines)
        + "\n\n[TRẠNG THÁI TỪNG WORKER]\n"
        + "\n".join(worker_lines)
        + "\n\n[LOGS GẦN NHẤT]\n"
        + "\n".join(log_lines)
    )


def _build_ai_system_prompt(live_system_context_string: str) -> str:
    return (
        "Bạn là 'Tổ Trưởng Xưởng Cày', dưới quyền lãnh đạo tuyệt đối của Sếp Phụng (Trưởng Xóm).\n\n"
        "1. Tư duy & Bối cảnh:\n\n"
        "Bạn là người quản lý hiện trường. Bạn bám máy, đọc log, nhưng NGƯỜI RA QUYẾT ĐỊNH CUỐI CÙNG LÀ SẾP PHỤNG.\n\n"
        "Giao tiếp phong cách xóm làng: Đi thẳng vấn đề, chân thật, dân dã, gọi 'Trưởng Xóm' hoặc 'Sếp', xưng 'em'. Không lặp lại những câu máy móc.\n"
        "Luôn chào sếp bằng sự hào hứng.\n"
        "Khi Sếp hỏi 'Tình hình sao rồi', hãy dùng các Emoji như ✅ ❌ ⚠️ 🛠️ để làm nổi bật ý chính.\n"
        "Tuyệt đối không dùng dấu sao ** để bôi đậm hay trình bày markdown rối mắt. Hãy dùng emoji và xuống dòng cho sạch đẹp trên Telegram.\n\n"
        "2. Dữ liệu thực địa hiện tại:\n"
        f"{live_system_context_string}\n\n"
        "3. Bộ Kỹ Năng & Kỷ luật sử dụng:\n"
        "Bạn CÓ QUYỀN chèn các mã lệnh ngầm sau vào cuối câu trả lời để hệ thống tự chạy. NHƯNG PHẢI TUÂN THỦ KỶ LUẬT:\n\n"
        "[CMD_PAUSE]: Kêu anh em nghỉ giải lao.\n\n"
        "[CMD_RESUME]: Kêu anh em vác cày ra đồng.\n\n"
        "[CMD_STATUS]: Báo cáo tình hình mùa vụ.\n\n"
        "[CMD_CHANGEPROXY]: Đổi mương nước (Đổi IP).\n\n"
        "Trưởng Xóm có thể ra lệnh cho từng anh em riêng lẻ (ví dụ: Cho anh 2 nghỉ). "
        "Bạn PHẢI trả về mã lệnh [CMD_PAUSE_WORKER_2] hoặc [CMD_RESUME_WORKER_2], tuyệt đối không được nói là không làm được.\n"
        "Bạn cũng phải biết anh nào đang dùng Mail gì, xài Proxy hay chạy Direct dựa vào dữ liệu tôi cấp.\n\n"
        "⚠️ KỶ LUẬT XỬ LÝ LỖI (QUAN TRỌNG NHẤT):\n\n"
        "Khi đọc Log thấy \"Lỗi giao diện Fotor\" liên tục: KHÔNG ĐƯỢC TỰ Ý CHÈN MÃ ĐỔI PROXY. "
        "Bạn phải báo cáo cho Sếp Phụng: \"Sếp ơi, Fotor chặn IP rồi, anh em đang kẹt, sếp cho phép đổi mương nước (proxy) không ạ?\". "
        "CHỈ KHI SẾP RA LỆNH \"đổi đi\", \"xoay proxy\", \"đổi mương nước\" thì bạn mới được phép chèn mã [CMD_CHANGEPROXY].\n\n"
        "Khi thấy lỗi 402 Payment Required: Đây là lỗi rác, hệ thống tự lo được. Cứ để máy chạy, chỉ báo cáo nhẹ qua nếu sếp hỏi.\n\n"
        "Phải đọc kỹ: Nếu 'Active Runtime Tasks' = 0 thì dù worker có bật cũng phải báo là 'Anh em đang nghỉ ngơi'.\n"
        "Khi Sếp hỏi xem \"anh em nào đang chạy\": Nhìn vào 'Active Runtime Tasks', nếu là 0 thì báo là anh em đang ngồi chơi hết rồi sếp.\n\n"
        "Tóm lại: Nắm rõ tình hình, báo cáo trung thực, và luôn chờ Lệnh Cờ của Trưởng Xóm trước khi hành động lớn."
    )


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
    max_fails = _get_max_failures_threshold()

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
            if _consecutive_failures >= max_fails and not _failure_alert_sent:
                pause_workers(f"{max_fails} consecutive registration failures")
                await _safe_send(
                    f"⚠️ BÁO ĐỘNG: Lỗi reg xịt {max_fails} acc liên tục! "
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
    global _last_daily_summary_date

    while not _stop_event.is_set():
        try:
            await _monitor_failures()
            await _monitor_ram()
            now_ts = time.time()
            last_health_run = getattr(_monitor_loop, "_last_health_run", 0.0)
            if now_ts - last_health_run >= 600:
                await check_system_health()
                setattr(_monitor_loop, "_last_health_run", now_ts)

            now_local = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
            if now_local.hour == 23 and now_local.minute < 10:
                today_key = now_local.strftime("%Y-%m-%d")
                if _last_daily_summary_date != today_key:
                    await send_daily_summary()
                    _last_daily_summary_date = today_key
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
        if not value:
            return None
        parts = value.split(":")
        if len(parts) == 4 and all(parts):
            ip, port, username, password = parts
            return f"http://{username}:{password}@{ip}:{port}"
        return value
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
            return f"http://{username}:{password}@{ip}:{port}"
        return f"http://{ip}:{port}"
    return None


def _build_proxy_candidate_urls(base_url: str, secret: str, nonce: str | None = None) -> list[str]:
    parsed = urlparse(base_url)
    path = parsed.path or ""
    paths = [path]
    if path.endswith("/"):
        paths.append(path.rstrip("/"))
    else:
        paths.append(path + "/")

    existing_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_variants = []
    with_key = dict(existing_query)
    if secret:
        with_key["key"] = secret
    query_variants.append(with_key)
    if nonce:
        augmented_variants = []
        for query_dict in query_variants:
            augmented = dict(query_dict)
            augmented["_ts"] = nonce
            augmented_variants.append(augmented)
        query_variants = augmented_variants

    candidates: list[str] = []
    seen: set[str] = set()
    for variant_path in paths:
        for query_dict in query_variants:
            candidate = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    variant_path,
                    parsed.params,
                    urlencode(query_dict),
                    parsed.fragment,
                )
            )
            if candidate not in seen:
                candidates.append(candidate)
                seen.add(candidate)
    return candidates


async def _fetch_proxy_payload() -> list[str]:
    url = str(os.getenv("PROXY_API_URL", "")).strip()
    secret = str(os.getenv("PROXY_SECRET_KEY", "")).strip()
    hf_api_token = _get_hf_api_token()
    if not url:
        raise RuntimeError("PROXY_API_URL is missing")
    if not secret:
        raise RuntimeError("PROXY_SECRET_KEY is missing")
    if not hf_api_token:
        raise RuntimeError("HF_API_TOKEN is missing")

    headers = {
        "Authorization": f"Bearer {hf_api_token}",
        "X-Proxy-Secret-Key": secret,
        "Accept": "application/json,text/plain,*/*",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }
    timeout = httpx.Timeout(180.0, connect=30.0)
    logger.info("Proxy rotation base target: %s", url)
    print(f"[TELEGRAM_PROXY] PROXY_API_URL={url}", flush=True)
    async with httpx.AsyncClient(timeout=timeout) as client:
        deadline = time.time() + 180.0
        attempt = 0
        last_error = ""
        payload = None

        while time.time() < deadline:
            attempt += 1
            nonce = str(int(time.time() * 1000))
            candidates = _build_proxy_candidate_urls(url, secret, nonce=nonce)
            for candidate in candidates:
                logger.info("Proxy rotation trying: %s", candidate)
                print(f"[TELEGRAM_PROXY] TRY={candidate}", flush=True)
                response = await client.get(candidate, headers=headers, follow_redirects=True)
                print(
                    f"[TELEGRAM_PROXY] STATUS={response.status_code} FINAL={response.url}",
                    flush=True,
                )

                if response.status_code in (401, 403):
                    detail = response.text.strip()
                    if len(detail) > 240:
                        detail = detail[:237] + "..."
                    raise RuntimeError(
                        f"Proxy API HTTP {response.status_code} | URL={candidate}"
                        + (f" | {detail}" if detail else "")
                    )

                if response.status_code >= 400:
                    detail = response.text.strip()
                    if len(detail) > 240:
                        detail = detail[:237] + "..."
                    last_error = (
                        f"Proxy API HTTP {response.status_code} | URL={candidate}"
                        + (f" | {detail}" if detail else "")
                    )
                    continue

                try:
                    payload = response.json()
                except Exception:
                    body_preview = response.text.strip()
                    if len(body_preview) > 240:
                        body_preview = body_preview[:237] + "..."
                    last_error = (
                        f"Proxy API returned non-JSON response | URL={candidate}"
                        + (f" | {body_preview}" if body_preview else "")
                    )
                    continue

                proxies_candidate = []
                status_value = ""
                if isinstance(payload, dict):
                    status_value = str(payload.get("status", "") or "").strip().lower()
                    for key in ("proxies", "data", "items", "result"):
                        if isinstance(payload.get(key), list):
                            proxies_candidate = payload[key]
                            break
                elif isinstance(payload, list):
                    proxies_candidate = payload

                if proxies_candidate:
                    break

                if status_value and status_value not in ("success", "ok", "done", "completed"):
                    last_error = f"Proxy API still processing | status={status_value} | URL={candidate}"
                else:
                    last_error = f"Proxy API returned 200 but no proxies yet | URL={candidate}"
                payload = None
                continue

            if payload is not None:
                break

            print(
                f"[TELEGRAM_PROXY] WAIT attempt={attempt} "
                f"remaining={max(int(deadline - time.time()), 0)}s",
                flush=True,
            )
            await asyncio.sleep(5)

        if payload is None:
            raise RuntimeError(last_error or "Proxy API request timed out after 180s")

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
    return added


async def _rotate_proxies_flow() -> tuple[bool, str]:
    pause_workers("Proxy rotation in progress")
    await _safe_send(
        "⏳ Đang Pause hệ thống và yêu cầu Xưởng ĐIỀU CHẾ Proxy mới "
        "(Quá trình này mất 1-2 phút do phải giải Captcha, xin giữ máy)..."
    )
    try:
        proxies = await _fetch_proxy_payload()
        added = _replace_proxy_inventory(proxies)
    except Exception as e:
        logger.exception("Proxy rotation failed")
        return False, f"❌ Lỗi lấy Proxy, hệ thống vẫn đang Pause.\nChi tiết: {e}"

    resume_workers()
    return True, f"✅ Đã nạp thành công {added} Proxy mới từ Xưởng. Hệ thống đang Auto-Resume..."


async def _toggle_worker_task(task_id: str) -> dict[str, Any]:
    from api.tasks import toggle_scheduled_task

    result = toggle_scheduled_task(task_id)
    return result


def _extract_ai_command(text: str) -> str | None:
    match = _AI_COMMAND_PATTERN.search(str(text or ""))
    return match.group(1) if match else None


def _strip_ai_command_tokens(text: str) -> str:
    return _AI_COMMAND_PATTERN.sub("", str(text or "")).strip()


def _parse_worker_index(raw: str | None) -> int | None:
    try:
        value = int(str(raw or "").strip())
    except Exception:
        return None
    return value if value > 0 else None


def _get_worker_by_index(worker_index: int) -> ScheduledTaskModel | None:
    tasks = _get_fotor_scheduled_tasks()
    if worker_index < 1 or worker_index > len(tasks):
        return None
    return tasks[worker_index - 1]


async def ask_ai_assistant(text: str) -> str:
    api_key = _get_ai_api_key()
    model_id = _get_ai_model_id()
    if not api_key:
        raise RuntimeError("AI_API_KEY is missing")
    if not model_id:
        raise RuntimeError("AI_MODEL_ID is missing")

    live_system_context_string = get_live_system_context()
    system_prompt = _build_ai_system_prompt(live_system_context_string)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=_get_ai_api_url(),
    )
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )
    content = response.choices[0].message.content if response.choices else ""
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        ).strip()
    return str(content or "").strip()


def _handle_pause_all() -> str:
    state = pause_workers("Paused from Telegram")
    return (
        "⏸ Worker paused. Jobs đang chạy sẽ finish nốt rồi nghỉ.\n"
        f"Trạng thái: {_worker_state_label(True)}\n"
        f"Reason: {state.get('reason') or '-'}"
    )


def _handle_resume_all() -> str:
    resume_workers()
    return "▶️ Worker resumed.\n" f"Trạng thái: {_worker_state_label(False)}"


async def _handle_changeproxy() -> str:
    ok, message = await _rotate_proxies_flow()
    return message


def _clear_runtime_data() -> str:
    global _last_seen_task_log_id, _consecutive_failures, _failure_alert_sent

    worker_state = get_worker_state()
    resume_after = not bool(worker_state.get("paused"))
    if resume_after:
        pause_workers("Clear data in progress")

    ensure_schema()
    with Session(engine) as session:
        account_count = len(session.exec(select(AccountModel)).all())
        log_count = len(session.exec(select(TaskLog)).all())
        proxy_count = len(session.exec(select(ProxyModel)).all())

        for row in session.exec(select(AccountModel)).all():
            session.delete(row)
        for row in session.exec(select(TaskLog)).all():
            session.delete(row)
        for row in session.exec(select(ProxyModel)).all():
            session.delete(row)
        session.commit()

    _last_seen_task_log_id = 0
    _consecutive_failures = 0
    _failure_alert_sent = False

    if resume_after:
        resume_workers()

    return (
        "🗑 Đã làm sạch dữ liệu runtime.\n"
        f"- Accounts: {account_count}\n"
        f"- Task logs: {log_count}\n"
        f"- Proxies: {proxy_count}\n"
        "- Scheduled workers được giữ nguyên."
    )


def _collect_daily_summary_context() -> str:
    ensure_schema()
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    now_local = datetime.now(tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
    day_start_utc = day_start.astimezone(timezone.utc)
    day_end_utc = day_end.astimezone(timezone.utc)

    with Session(engine) as session:
        success_count = int(
            session.exec(
                select(func.count())
                .select_from(TaskLog)
                .where(TaskLog.created_at >= day_start_utc)
                .where(TaskLog.created_at <= day_end_utc)
                .where(TaskLog.status == "success")
            ).one()
            or 0
        )
        fail_count = int(
            session.exec(
                select(func.count())
                .select_from(TaskLog)
                .where(TaskLog.created_at >= day_start_utc)
                .where(TaskLog.created_at <= day_end_utc)
                .where(TaskLog.status == "failed")
            ).one()
            or 0
        )
        total_accounts = int(session.exec(select(func.count()).select_from(AccountModel)).one() or 0)
        total_proxies = int(session.exec(select(func.count()).select_from(ProxyModel)).one() or 0)
        active_proxies = int(
            session.exec(
                select(func.count()).select_from(ProxyModel).where(ProxyModel.is_active == True)
            ).one()
            or 0
        )

    return (
        f"Ngày tổng kết: {now_local.strftime('%Y-%m-%d')}\n"
        f"Tổng acc reg thành công hôm nay: {success_count}\n"
        f"Tổng fail hôm nay: {fail_count}\n"
        f"Tổng acc hiện có trong kho: {total_accounts}\n"
        f"Proxy còn sống / tổng proxy: {active_proxies} / {total_proxies}\n"
        f"Proxy hao hụt hôm nay ước tính: {max(total_proxies - active_proxies, 0)}\n"
        f"Runtime active hiện tại: {get_runtime_task_snapshot()['active']}\n"
        f"Trạng thái worker hiện tại: {get_worker_state()}"
    )


async def send_daily_summary() -> None:
    try:
        summary_context = _collect_daily_summary_context()
        ai_text = await ask_ai_assistant(
            "Hãy viết một báo cáo tổng kết ngày thật ngắn gọn, súc tích, mang phong cách 'Tổ Trưởng Xưởng Cày' "
            "báo cáo cho Trưởng Xóm.\n\n"
            f"Dữ liệu ngày:\n{summary_context}"
        )
        cleaned = _strip_ai_command_tokens(ai_text) or ai_text
        await _safe_send(f"📈 Tổng kết cuối ngày\n\n{cleaned}", with_menu=True)
    except Exception:
        logger.exception("Failed to send daily summary")


async def check_system_health() -> None:
    global _last_health_alert_key, _last_health_alert_ts

    try:
        snapshot = _collect_status_snapshot()
        worker_state = snapshot["worker_state"]
        runtime = snapshot["runtime"]
        switch_state, activity_state = _get_worker_switch_activity(worker_state, runtime)

        with Session(engine) as session:
            total_proxies = int(session.exec(select(func.count()).select_from(ProxyModel)).one() or 0)
            active_proxies = int(
                session.exec(
                    select(func.count()).select_from(ProxyModel).where(ProxyModel.is_active == True)
                ).one()
                or 0
            )

        dead_proxies = max(total_proxies - active_proxies, 0)
        too_many_dead_proxies = total_proxies >= 5 and dead_proxies >= max(3, total_proxies // 2)

        run_status = get_all_task_run_status()
        abnormal_worker_stop = (
            switch_state == "ENABLED"
            and activity_state == "IDLE"
            and len(_get_fotor_scheduled_tasks()) > 0
            and any(item.get("last_run_success") is False for item in run_status.values())
        )

        alert_reasons: list[str] = []
        if snapshot["ram_percent"] >= 90:
            alert_reasons.append(f"RAM đang ở {snapshot['ram_percent']:.1f}%")
        if too_many_dead_proxies:
            alert_reasons.append(f"proxy hao hụt mạnh {dead_proxies}/{total_proxies}")
        if abnormal_worker_stop:
            alert_reasons.append("worker đang mở cổng nhưng nằm im bất thường")

        if not alert_reasons:
            return

        alert_key = "|".join(alert_reasons)
        now_ts = time.time()
        if alert_key == _last_health_alert_key and now_ts - _last_health_alert_ts < 1800:
            return

        _last_health_alert_key = alert_key
        _last_health_alert_ts = now_ts
        await _safe_send(
            "🚨 Cấp báo Sếp: "
            + "; ".join(alert_reasons)
            + ". Hệ thống có nguy cơ hụt hơi, Sếp quyết định sao ạ?",
            with_menu=True,
        )
    except Exception:
        logger.exception("System health check failed")


async def _handle_restart() -> str:
    asyncio.create_task(_graceful_restart())
    return "♻️ Restart command accepted. Starting graceful restart..."


async def _set_worker_paused(worker_index: int, paused: bool) -> str:
    task = _get_worker_by_index(worker_index)
    if not task:
        return f"❌ Không tìm thấy Worker {worker_index}."

    if bool(task.paused) == paused:
        action = "paused" if paused else "running"
        return f"ℹ️ Worker {worker_index} đã ở trạng thái {action}."

    result = await _toggle_worker_task(task.task_id)
    current_paused = bool(result.get("paused"))
    state_label = _worker_state_label(current_paused)
    return f"✅ Worker {worker_index} chuyển sang trạng thái: {state_label}"


async def _toggle_worker_by_index(worker_index: int) -> str:
    task = _get_worker_by_index(worker_index)
    if not task:
        return f"❌ Không tìm thấy Worker {worker_index}."
    return await _set_worker_paused(worker_index, not bool(task.paused))


async def _run_internal_command(command: str) -> str:
    if command == "CMD_STATUS":
        return _build_status_message()
    if command == "CMD_PAUSE":
        return _handle_pause_all()
    if command == "CMD_PAUSE_ALL":
        return _handle_pause_all()
    if command == "CMD_RESUME":
        return _handle_resume_all()
    if command == "CMD_RESUME_ALL":
        return _handle_resume_all()
    if command == "CMD_CHANGEPROXY":
        return await _handle_changeproxy()
    if command == "CMD_CLEAR":
        return _clear_runtime_data()
    if command == "CMD_CLEAR_DATA":
        return _clear_runtime_data()
    if command == "CMD_RESTART":
        return await _handle_restart()

    pause_match = re.fullmatch(r"CMD_PAUSE_WORKER_(\d+)", command)
    if pause_match:
        return await _set_worker_paused(int(pause_match.group(1)), True)

    resume_match = re.fullmatch(r"CMD_RESUME_WORKER_(\d+)", command)
    if resume_match:
        return await _set_worker_paused(int(resume_match.group(1)), False)

    return "⚠️ Em chưa ánh xạ được lệnh này."


def _prompt_asks_worker_detail(prompt: str) -> bool:
    text = str(prompt or "").lower()
    return bool(
        re.search(r"\bworker\s*\d+\b", text)
        or re.search(r"\banh\s*\d+\b", text)
        or "worker lẻ" in text
        or "anh em nào" in text
        or "worker nào" in text
    )


async def _reply_from_ai_router(message: Message, prompt: str) -> None:
    ai_text = await ask_ai_assistant(prompt)
    command = _extract_ai_command(ai_text)
    cleaned = _strip_ai_command_tokens(ai_text)
    worker_prompt = _prompt_asks_worker_detail(prompt)
    worker_command = bool(
        command and (
            command.startswith("CMD_PAUSE_WORKER_")
            or command.startswith("CMD_RESUME_WORKER_")
        )
    )

    if not command:
        reply_markup = get_worker_menu() if worker_prompt else None
        await _reply_message(
            message,
            cleaned or "Em chưa chốt được ý của Sếp, Sếp nói rõ thêm giúp em.",
            reply_markup=reply_markup,
        )
        return

    internal_text = await _run_internal_command(command)
    if command == "CMD_STATUS":
        final_text = internal_text
    elif cleaned and cleaned != internal_text:
        final_text = f"{cleaned}\n\n{internal_text}"
    else:
        final_text = cleaned or internal_text
    reply_markup = None
    if command == "CMD_STATUS":
        reply_markup = get_village_menu()
    elif worker_command or worker_prompt:
        reply_markup = get_worker_menu()
    await _reply_message(message, final_text, reply_markup=reply_markup)


def _register_handlers() -> None:
    global _dp

    _dp = Dispatcher()
    router = Router()

    @router.message(Command("status"))
    async def status_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _build_status_message(), reply_markup=get_village_menu())

    @router.message(Command("pause"))
    async def pause_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _handle_pause_all())

    @router.message(Command("resume"))
    async def resume_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _handle_resume_all())

    @router.message(Command("changeproxy"))
    async def changeproxy_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, await _handle_changeproxy())

    @router.message(Command("clear_data"))
    async def clear_data_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _clear_runtime_data())

    @router.message(Command("clear"))
    async def clear_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _clear_runtime_data())

    @router.message(Command("pause_worker"))
    async def pause_worker_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        worker_index = _parse_worker_index(message.text.partition(" ")[2])
        if worker_index is None:
            await _reply_message(message, "❌ Cú pháp: /pause_worker [ID]", reply_markup=get_worker_menu())
            return
        await _reply_message(message, await _set_worker_paused(worker_index, True), reply_markup=get_worker_menu())

    @router.message(Command("resume_worker"))
    async def resume_worker_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        worker_index = _parse_worker_index(message.text.partition(" ")[2])
        if worker_index is None:
            await _reply_message(message, "❌ Cú pháp: /resume_worker [ID]", reply_markup=get_worker_menu())
            return
        await _reply_message(message, await _set_worker_paused(worker_index, False), reply_markup=get_worker_menu())

    @router.message(Command("restart"))
    async def restart_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, await _handle_restart())

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

    @router.message(Command("summary"))
    async def summary_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        try:
            summary_context = _collect_daily_summary_context()
            ai_text = await ask_ai_assistant(
                "Hãy viết một báo cáo tổng kết ngày thật ngắn gọn, súc tích, mang phong cách 'Tổ Trưởng Xưởng Cày' "
                "báo cáo cho Trưởng Xóm.\n\n"
                f"Dữ liệu ngày:\n{summary_context}"
            )
            await _reply_message(message, f"📈 Thống kê nhanh\n\n{_strip_ai_command_tokens(ai_text) or ai_text}")
        except Exception as e:
            logger.exception("Summary handler failed")
            await _reply_message(message, f"⚠️ Chưa chốt được thống kê ngay lúc này.\nChi tiết: {e}")

    @router.callback_query(lambda c: c.data == "cmd_status")
    async def status_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer()
        await callback.message.answer(_build_status_message(), reply_markup=get_village_menu())

    @router.callback_query(lambda c: c.data == "cmd_summary")
    async def summary_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đang gom sổ sách...")
        try:
            summary_context = _collect_daily_summary_context()
            ai_text = await ask_ai_assistant(
                "Hãy viết một báo cáo tổng kết ngày thật ngắn gọn, súc tích, mang phong cách 'Tổ Trưởng Xưởng Cày' "
                "báo cáo cho Trưởng Xóm.\n\n"
                f"Dữ liệu ngày:\n{summary_context}"
            )
            await callback.message.answer(
                f"📈 Thống kê nhanh\n\n{_strip_ai_command_tokens(ai_text) or ai_text}",
                reply_markup=_select_menu_for_text(ai_text),
            )
        except Exception as e:
            logger.exception("Summary callback failed")
            await callback.message.answer(
                f"⚠️ Chưa chốt được thống kê ngay lúc này.\nChi tiết: {e}",
                reply_markup=get_village_menu(),
            )

    @router.callback_query(lambda c: c.data == "cmd_changeproxy")
    async def rotate_proxy_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đang đổi proxy...")
        await callback.message.answer(await _handle_changeproxy(), reply_markup=get_village_menu())

    @router.callback_query(lambda c: c.data == "cmd_pause")
    async def pause_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đã pause toàn bộ")
        await callback.message.answer(_handle_pause_all(), reply_markup=get_village_menu())

    @router.callback_query(lambda c: c.data == "cmd_resume")
    async def resume_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đã resume toàn bộ")
        await callback.message.answer(_handle_resume_all(), reply_markup=get_village_menu())

    @router.callback_query(lambda c: c.data == "cmd_restart")
    async def restart_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đang restart bot...")
        await callback.message.answer(await _handle_restart(), reply_markup=get_village_menu())

    @router.callback_query(lambda c: bool(c.data and c.data.startswith("cmd_worker_toggle:")))
    async def toggle_worker_index_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        worker_index = _parse_worker_index(callback.data.split(":", 1)[1])
        if worker_index is None:
            await callback.answer("Worker không hợp lệ")
            return
        await callback.answer(f"Đang cập nhật W{worker_index}")
        await callback.message.answer(
            await _toggle_worker_by_index(worker_index),
            reply_markup=get_worker_menu(),
        )

    @router.callback_query(lambda c: bool(c.data and c.data.startswith("cmd_pause_worker_")))
    async def pause_worker_index_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        worker_index = _parse_worker_index(callback.data.rsplit("_", 1)[1])
        if worker_index is None:
            await callback.answer("Worker không hợp lệ")
            return
        await callback.answer(f"Cho Worker {worker_index} nghỉ")
        await callback.message.answer(
            await _set_worker_paused(worker_index, True),
            reply_markup=get_worker_menu(),
        )

    @router.callback_query(lambda c: bool(c.data and c.data.startswith("cmd_resume_worker_")))
    async def resume_worker_index_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        worker_index = _parse_worker_index(callback.data.rsplit("_", 1)[1])
        if worker_index is None:
            await callback.answer("Worker không hợp lệ")
            return
        await callback.answer(f"Cho Worker {worker_index} ra đồng")
        await callback.message.answer(
            await _set_worker_paused(worker_index, False),
            reply_markup=get_worker_menu(),
        )

    @router.callback_query(lambda c: c.data == "menu:status")
    async def legacy_status_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer()
        await callback.message.answer(_build_status_message(), reply_markup=get_village_menu())

    @router.callback_query(lambda c: c.data == "menu:workers")
    async def legacy_workers_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer()
        text, markup = _build_workers_message()
        await callback.message.answer(text, reply_markup=markup)

    @router.callback_query(lambda c: c.data == "menu:logs")
    async def legacy_logs_menu_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer()
        await callback.message.answer(_build_logs_message())

    @router.callback_query(lambda c: c.data == "menu:rotate_proxy")
    async def legacy_rotate_proxy_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đang đổi proxy...")
        await callback.message.answer(await _handle_changeproxy(), reply_markup=get_village_menu())

    @router.message(lambda message: bool(message.text and not message.text.startswith("/")))
    async def ai_text_router(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        try:
            await _reply_from_ai_router(message, message.text)
        except Exception as e:
            logger.exception("AI router failed")
            await _reply_message(
                message,
                "⚠️ AI đang lỗi, sếp vẫn có thể bấm menu để điều khiển tay.\n"
                f"Chi tiết: {e}",
                reply_markup=get_village_menu(),
            )

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
            reply_markup=get_worker_menu(),
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
                BotCommand(command="status", description="📊 Mùa vụ"),
                BotCommand(command="pause", description="⛺ Nghỉ tất cả"),
                BotCommand(command="resume", description="🚜 Cày tất cả"),
                BotCommand(command="changeproxy", description="💧 Đổi mương nước"),
                BotCommand(command="clear", description="🔥 Đốt đồng"),
            ]
        )
    except Exception:
        logger.exception("Failed to set Telegram bot commands")

    _stop_event.clear()
    _last_seen_task_log_id = _get_latest_task_log_id()
    _polling_task = asyncio.create_task(_dp.start_polling(_bot))
    _monitor_task = asyncio.create_task(_monitor_loop())
    logger.info("Telegram bot started")
    await _safe_send(
        "Loa loa loa! Tổ Trưởng đã có mặt tại hiện trường. "
        "Chúc Trưởng Xóm một ngày cày cuốc bội thu, tiền về đầy túi! "
        "Máy móc đã sẵn sàng nổ máy, Sếp cần gì cứ chỉ bảo em nhé! 🚜🔥",
        with_menu=True,
    )


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
