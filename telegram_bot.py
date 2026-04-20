from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from openai import AsyncOpenAI
from sqlalchemy import func
from sqlmodel import Session, select

from api.tasks import get_runtime_task_snapshot
from core.config_store import config_store
from core.db import AccountModel, ProxyModel, ScheduledTaskModel, TaskLog, engine, ensure_schema
from core.scheduler import get_all_task_run_status, get_running_scheduled_tasks, scheduler
from services.captcha_finance import get_dbc_balance
from services.worker_control import get_worker_state, is_worker_paused, pause_workers, resume_workers


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
_last_throttle_action_ts = 0.0
_smart_sleep_task: asyncio.Task | None = None
_is_scouting = False
_scout_worker_index: int | None = None
_smart_sleep_restore_paused: dict[int, bool] = {}
_auto_proxy_rotation_timestamps: list[float] = []
_auto_proxy_rotation_timestamps: list[float] = []
_AI_COMMAND_PATTERN = re.compile(r"\[(CMD_[a-zA-Z0-9_]+)\]", re.IGNORECASE)
user_chat_history: dict[str, list[dict[str, str]]] = {}
MAX_HISTORY = 10
CLEAR_MEMORY_BUTTON = "🧹 Xóa trí nhớ Bot"


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


def _get_ai_model_candidates() -> list[str]:
    raw = _get_ai_model_id()
    candidates = [item.strip() for item in raw.split(",") if item.strip()]
    return candidates


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
    total_gb = 16.0
    process = psutil.Process(os.getpid())
    used_bytes = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            used_bytes += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    used_gb = round(used_bytes / (1024 ** 3), 2)
    percent = round((used_gb / total_gb) * 100, 1)
    return {
        "used_bytes": float(used_bytes),
        "limit_bytes": float(total_gb * (1024 ** 3)),
        "used_gb": float(used_gb),
        "total_gb": float(total_gb),
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
            [
                InlineKeyboardButton(text="💧 Đổi mương", callback_data="cmd_changeproxy"),
                InlineKeyboardButton(text="🗑️ Xóa Logs", callback_data="cmd_clear_logs"),
            ],
        ]
    )


def get_proxy_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💧 Đổi Proxy", callback_data="cmd_changeproxy"),
                InlineKeyboardButton(text="📊 Status", callback_data="cmd_status"),
            ]
        ]
    )


def get_casual_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Xem tình hình", callback_data="cmd_status")]
        ]
    )


def get_worker_menu(worker_index: int | None = None) -> InlineKeyboardMarkup:
    tasks = _get_fotor_scheduled_tasks()
    rows: list[list[InlineKeyboardButton]] = []

    if worker_index is not None:
        task = _get_worker_by_index(worker_index)
        if not task:
            return InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="📊 Mùa vụ", callback_data="cmd_status")]]
            )
        if bool(task.paused):
            rows.append([InlineKeyboardButton(text=f"🚜 Bật Worker {worker_index}", callback_data=f"cmd_resume_worker_{worker_index}")])
        else:
            rows.append([InlineKeyboardButton(text=f"⛺ Tắt Worker {worker_index}", callback_data=f"cmd_pause_worker_{worker_index}")])
        rows.append(
            [
                InlineKeyboardButton(text="🌐 Chạy Direct (Ko tốn Proxy)", callback_data=f"cmd_worker_direct_{worker_index}"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text="🛡️ Chạy Proxy (An toàn)", callback_data=f"cmd_worker_proxy_{worker_index}"),
            ]
        )
        rows.append([InlineKeyboardButton(text="📊 Mùa vụ", callback_data="cmd_status")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    for index, task in enumerate(tasks, start=1):
        if bool(task.paused):
            rows.append([InlineKeyboardButton(text=f"🚜 Bật Worker {index}", callback_data=f"cmd_resume_worker_{index}")])
        else:
            rows.append([InlineKeyboardButton(text=f"⛺ Tắt Worker {index}", callback_data=f"cmd_pause_worker_{index}")])
        rows.append(
            [
                InlineKeyboardButton(text=f"🌐 W{index} Direct", callback_data=f"cmd_worker_direct_{index}"),
                InlineKeyboardButton(text=f"🛡️ W{index} Proxy", callback_data=f"cmd_worker_proxy_{index}"),
            ]
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


def _get_fail_safe_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏸ Dừng Máy", callback_data="cmd_pause"),
                InlineKeyboardButton(text="💧 Đổi Proxy", callback_data="cmd_changeproxy"),
            ]
        ]
    )


def _get_changeproxy_only_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💧 Đổi Proxy", callback_data="cmd_changeproxy")]
        ]
    )


def _select_menu_for_text(text: str | None = None) -> InlineKeyboardMarkup:
    content = str(text or "")
    if len(content) > 900 or content.count("\n") > 16:
        return _compact_village_menu()
    return get_village_menu()


def get_chat_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📊 Báo cáo tình hình hôm nay"),
                KeyboardButton(text="🔮 Dự báo sức tải máy móc"),
            ],
            [
                KeyboardButton(text="⚠️ Dạo này có lỗi gì không em?"),
                KeyboardButton(text="💧 Xoay proxy mới đi em"),
            ],
            [
                KeyboardButton(text=CLEAR_MEMORY_BUTTON),
                KeyboardButton(text="🗑️ Xóa sạch Lịch sử Logs"),
            ],
        ],
        resize_keyboard=True,
    )


def _default_reply_keyboard() -> ReplyKeyboardMarkup:
    return get_chat_keyboard()


def _get_history_key(user_id: str | int | None) -> str | None:
    if user_id is None:
        return None
    value = str(user_id).strip()
    return value or None


def _get_user_history(user_id: str | int | None) -> list[dict[str, str]]:
    history_key = _get_history_key(user_id)
    if history_key is None:
        return []
    return user_chat_history.setdefault(history_key, [])


def _append_user_history(user_id: str | int | None, role: str, content: str) -> None:
    history_key = _get_history_key(user_id)
    if history_key is None:
        return
    history = user_chat_history.setdefault(history_key, [])
    history.append({"role": role, "content": str(content or "")})
    while len(history) > MAX_HISTORY:
        history.pop(0)


def _clear_user_history(user_id: str | int | None) -> None:
    history_key = _get_history_key(user_id)
    if history_key is None:
        return
    user_chat_history[history_key] = []


def _reset_runtime_counters() -> None:
    global _consecutive_failures, _failure_alert_sent, _last_ram_alert_ts
    global _last_health_alert_key, _last_health_alert_ts, _last_throttle_action_ts
    global _is_scouting, _scout_worker_index, _smart_sleep_restore_paused
    global _auto_proxy_rotation_timestamps
    _consecutive_failures = 0
    _failure_alert_sent = False
    _last_ram_alert_ts = 0.0
    _last_health_alert_key = ""
    _last_health_alert_ts = 0.0
    _last_throttle_action_ts = 0.0
    _is_scouting = False
    _scout_worker_index = None
    _smart_sleep_restore_paused = {}
    _auto_proxy_rotation_timestamps = []


async def _safe_send(
    text: str,
    *,
    with_menu: bool = False,
    reply_markup: Any = None,
) -> None:
    if not _bot or not is_enabled():
        return
    try:
        await _bot.send_message(
            chat_id=int(_get_admin_chat_id()),
            text=text,
            reply_markup=reply_markup if reply_markup is not None else (_select_menu_for_text(text) if with_menu else _default_reply_keyboard()),
        )
    except Exception:
        logger.exception("Failed to send Telegram message")


async def _reply_message(
    message: Message,
    text: str,
    *,
    with_menu: bool = False,
    reply_markup: Any = None,
) -> None:
    await message.answer(
        text,
        reply_markup=reply_markup if reply_markup is not None else (_select_menu_for_text(text) if with_menu else _default_reply_keyboard()),
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
        "ram_used_gb": float(mem["used_gb"]),
        "ram_total_gb": float(mem["total_gb"]),
        "worker_state": worker_state,
        "runtime": runtime,
        "running_scheduled": running_scheduled,
    }


def _build_status_message() -> str:
    snapshot = _collect_status_snapshot()
    pause_reason = snapshot["worker_state"].get("reason") or "-"
    dbc_balance = get_dbc_balance()
    return (
        "📊 AutoReg Fotor Status\n"
        f"- Acc đã Reg / Acc đã đủ Ref: {snapshot['account_total']} / {snapshot['max_ref_accounts']}\n"
        f"- Reg fail logs: {snapshot['failed_count']}\n"
        f"- CPU / RAM: {snapshot['cpu_percent']:.1f}% / {snapshot['ram_percent']:.1f}% "
        f"({snapshot['ram_used_gb']:.1f}GB / {snapshot['ram_total_gb']:.1f}GB)\n"
        f"- Auto xoay Proxy trong 1h: {_get_auto_proxy_rotation_count()}/5\n"
        f"- DeathByCaptcha: ${dbc_balance:.3f}\n"
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
        network_mode = _format_worker_network_label(task.get_extra().get("network_mode"))
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
            f"- Worker {idx}: {state} | mạng={network_mode} | count={task.count} | every {task.interval_value} {task.interval_type} | last={last_result}"
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
        try:
            detail = json.loads(log.detail_json or "{}")
        except Exception:
            detail = {}
        network_mode = _normalize_worker_network_mode(detail.get("network_mode"))
        network_tag = "🌐 Direct" if network_mode == "direct" else "🛡️ Proxy"
        email = str(log.email or "-")
        created = log.created_at.strftime("%m-%d %H:%M") if log.created_at else "-"
        err = str(log.error or "").strip().replace("\n", " ")
        if len(err) > 90:
            err = err[:87] + "..."
        if status == "success":
            lines.append(f"- [{created}] {status} | [{network_tag}] | {email}")
        else:
            lines.append(f"- [{created}] {status} | [{network_tag}] | {err or email}")
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
        try:
            detail = json.loads(log.detail_json or "{}")
        except Exception:
            detail = {}
        network_mode = _normalize_worker_network_mode(detail.get("network_mode"))
        network_tag = "🌐 Direct" if network_mode == "direct" else "🛡️ Proxy"
        email = str(log.email or "-")
        err = str(log.error or "").strip().replace("\n", " ")
        if len(err) > 160:
            err = err[:157] + "..."
        line = f"[{created}] {status} | [{network_tag}] | {email}"
        if err:
            line += f" | err={err}"
        lines.append(line)
    return lines


def _get_worker_switch_activity(worker_state: dict[str, Any], runtime: dict[str, Any]) -> tuple[str, str]:
    switch_state = "DISABLED" if bool(worker_state.get("paused")) else "ENABLED"
    activity_state = "RUNNING" if int(runtime.get("active") or 0) > 0 else "IDLE"
    return switch_state, activity_state


def _normalize_worker_network_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"direct", "proxy"} else "proxy"


def _format_worker_network_label(mode: str | None) -> str:
    normalized = _normalize_worker_network_mode(mode)
    return "Đang cày chay Direct" if normalized == "direct" else "Đang qua Proxy"


def _prune_auto_proxy_rotation_budget(now_ts: float | None = None) -> None:
    global _auto_proxy_rotation_timestamps
    current = now_ts or time.time()
    _auto_proxy_rotation_timestamps = [
        ts for ts in _auto_proxy_rotation_timestamps if current - ts < 3600
    ]


def _get_auto_proxy_rotation_count() -> int:
    _prune_auto_proxy_rotation_budget()
    return len(_auto_proxy_rotation_timestamps)


def _record_auto_proxy_rotation() -> int:
    _prune_auto_proxy_rotation_budget()
    _auto_proxy_rotation_timestamps.append(time.time())
    _prune_auto_proxy_rotation_budget()
    return len(_auto_proxy_rotation_timestamps)


def _collect_hourly_performance_summary() -> str:
    ensure_schema()
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    network_stats = {
        "direct": {"success": 0, "failed": 0},
        "proxy": {"success": 0, "failed": 0},
    }
    worker_stats: dict[int, dict[str, Any]] = {}

    with Session(engine) as session:
        logs = session.exec(
            select(TaskLog)
            .where(TaskLog.platform == "fotor")
            .where(TaskLog.created_at >= one_hour_ago)
            .order_by(TaskLog.created_at.asc())
        ).all()

    for log in logs:
        try:
            detail = json.loads(log.detail_json or "{}")
        except Exception:
            detail = {}
        network_mode = _normalize_worker_network_mode(detail.get("network_mode"))
        status = str(log.status or "").lower()
        if status == "success":
            network_stats[network_mode]["success"] += 1
        elif status == "failed":
            network_stats[network_mode]["failed"] += 1

        worker_index = detail.get("worker_index")
        try:
            worker_index = int(worker_index)
        except Exception:
            worker_index = None
        if worker_index is None or worker_index <= 0:
            continue

        bucket = worker_stats.setdefault(
            worker_index,
            {"network_mode": network_mode, "reg": 0, "failed": 0},
        )
        bucket["network_mode"] = network_mode
        if status == "success":
            bucket["reg"] += 1
        elif status == "failed":
            bucket["failed"] += 1

    direct = network_stats["direct"]
    proxy = network_stats["proxy"]
    worker_parts: list[str] = []
    for item in _get_worker_snapshots():
        stats = worker_stats.get(item["index"], {"reg": 0, "failed": 0, "network_mode": item["network_mode"]})
        worker_parts.append(
            f"Worker {item['index']} (Mạng: {'Direct' if item['network_mode'] == 'direct' else 'Proxy'}, Reg: {stats['reg']} acc, Lỗi: {stats.get('failed', 0)})"
        )
    if not worker_parts:
        worker_parts.append("Chưa có worker nào hoạt động trong 1 giờ qua")

    return (
        "[📊 THỐNG KÊ HIỆU SUẤT TRONG 1 GIỜ QUA]\n"
        f"- Tỉ lệ thành công theo Mạng: Direct (Thành công {direct['success']} / Lỗi {direct['failed']}) | "
        f"Proxy (Thành công {proxy['success']} / Lỗi {proxy['failed']})\n"
        f"- Hiệu suất từng máy: {' | '.join(worker_parts)}"
    )


def _get_worker_snapshots() -> list[dict[str, Any]]:
    tasks = _get_fotor_scheduled_tasks()
    running_map = get_running_scheduled_tasks()
    run_status = get_all_task_run_status()
    runtime = get_runtime_task_snapshot()
    switch_state, activity_state = _get_worker_switch_activity(get_worker_state(), runtime)
    snapshots: list[dict[str, Any]] = []

    for idx, task in enumerate(tasks, start=1):
        extra = task.get_extra()
        mail_provider = str(extra.get("mail_provider") or config_store.get("mail_provider", "duckmail") or "duckmail").strip()
        network_mode = _normalize_worker_network_mode(extra.get("network_mode"))
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
                "network_label": _format_worker_network_label(network_mode),
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
    hourly_summary = _collect_hourly_performance_summary()
    dbc_balance = get_dbc_balance()

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
        f"- Ngân sách tự xoay Proxy đã dùng trong 1h: {_get_auto_proxy_rotation_count()}/5",
        f"- Cập nhật lúc: {_now_str()}",
    ]
    if runtime["active"] > 0:
        avg_cpu = round(snapshot["cpu_percent"] / runtime["active"], 1)
        avg_ram = round(snapshot["ram_percent"] / runtime["active"], 1)
        stats_lines.append(
            f"- Thông số động: 1 máy hiện tại đang ngốn trung bình {avg_cpu}% CPU và {avg_ram}% RAM."
        )
    else:
        stats_lines.append("- Thông số động: Chưa có máy nào đang cày để nhẩm tải.")

    # === QUERY TRẠNG THÁI THỰC TẾ TỪ DB (không dùng cache/snapshot) ===
    global_paused = bool(worker_state.get("paused"))
    worker_lines = []
    ensure_schema()
    with Session(engine) as session:
        db_tasks = session.exec(
            select(ScheduledTaskModel)
            .where(ScheduledTaskModel.platform == "fotor")
            .order_by(ScheduledTaskModel.created_at.asc())
        ).all()
        for idx, db_task in enumerate(db_tasks, start=1):
            task_paused_in_db = bool(db_task.paused)
            db_extra = db_task.get_extra()
            db_network_mode = _normalize_worker_network_mode(db_extra.get("network_mode"))
            # Trạng thái thực: Paused nếu task bị pause HOẶC global switch bị pause
            if task_paused_in_db:
                real_status = "Paused"
            elif global_paused:
                real_status = "Paused (Global Switch)"
            elif db_task.task_id in snapshot.get("running_scheduled", {}):
                real_status = "Running"
            else:
                real_status = "Running"
            net_mode = "Direct" if db_network_mode == "direct" else "Proxy"
            worker_lines.append(
                f"- Worker {idx} (ID: {db_task.task_id}): Trạng thái: [{real_status}] | Mạng: [{net_mode}]"
            )
    if not worker_lines:
        worker_lines.append("- Chưa có worker nào trong hệ thống.")

    log_lines = recent_logs or ["- Chưa có log mới."]
    return (
        "[CONFIG HIỆN TẠI]\n"
        + "\n".join(config_lines)
        + "\n\n[THỐNG KÊ CƠ BẢN]\n"
        + "\n".join(stats_lines)
        + "\n\n"
        + hourly_summary
        + "\n\n[DANH SÁCH MÁY CÀY (WORKERS)]\n"
        + "\n".join(worker_lines)
        + "\n\n[LOGS GẦN NHẤT]\n"
        + "\n".join(log_lines)
        + f"\n\n[💰 TÀI CHÍNH] DeathByCaptcha: ${dbc_balance:.3f} USD"
    )


def _build_ai_system_prompt(live_system_context_string: str) -> str:
    return (
        "Bạn là Tổ Trưởng Xưởng Cày. Sếp Phụng rất thích nghe bạn dự đoán hiệu suất. KHÔNG BAO GIỜ từ chối dự đoán.\n\n"
        "Bạn quản hiện trường, đọc log, bám máy, nói ngắn gọn kiểu anh em ngoài xưởng. Xưng em, gọi Sếp hoặc Trưởng Xóm.\n"
        "Tuyệt đối không dùng dấu sao ** để bôi đậm hay trình bày markdown rối mắt. Hãy dùng emoji và xuống dòng cho sạch đẹp trên Telegram.\n\n"
        "TUYỆT ĐỐI KHÔNG viết các mã [CMD_...] vào giữa câu chat để tránh tự kích hoạt lệnh nhầm. "
        "Chỉ xuất mã lệnh ở CUỐI CÙNG của câu trả lời nếu bạn thực sự muốn hệ thống chạy lệnh đó.\n\n"
        "2. Dữ liệu thực địa hiện tại:\n"
        f"{live_system_context_string}\n\n"
        "3. Kỹ năng dự đoán:\n"
        "Hãy dùng 'Thông số động' trong Báo Cáo Live để nhẩm tính. Nếu Sếp giả định chạy X máy, hãy nhân X với thông số động đó và đưa ra lời khuyên thực tế. "
        "Ví dụ: 'Sếp mà cắm 5 máy thì CPU khả năng chạm 90% đấy, coi chừng khét máy!'.\n\n"
        "4. Bộ lệnh ngầm:\n"
        "CẢNH BÁO TỐI QUAN TRỌNG: Lời nói của bạn KHÔNG CÓ TÁC DỤNG nếu KHÔNG CÓ MÃ LỆNH. "
        "Bất cứ khi nào bạn báo cáo 'Đã bật', 'Đã tắt', 'Đã chuyển mạng', hoặc TỰ ĐỘNG xoay proxy, "
        "BẠN BẮT BUỘC PHẢI IN RA CÁC MÃ LỆNH TƯƠNG ỨNG Ở CUỐI CÙNG CỦA CÂU TRẢ LỜI. Đừng bao giờ giả vờ đã làm nếu chưa xuất mã lệnh!\n\n"
        "[CMD_PAUSE] để dừng máy toàn bộ.\n"
        "[CMD_RESUME] để chạy tiếp toàn bộ.\n"
        "[CMD_STATUS] để báo cáo tổng quan.\n"
        "[CMD_CHANGEPROXY] để đổi proxy.\n"
        "Bật máy: [CMD_START_WORKER_1] (Thay 1 bằng số thứ tự máy)\n"
        "Tắt máy: [CMD_PAUSE_WORKER_1]\n"
        "Đổi sang Direct: [CMD_NETWORK_DIRECT_1]\n"
        "Đổi sang Proxy: [CMD_NETWORK_PROXY_1]\n\n"
        "Ví dụ: Nếu Sếp bảo 'Bật máy 2 chạy chạy Direct', bạn PHẢI trả lời chính xác theo form này:\n"
        "'Dạ em bật máy 2 chạy Direct đây ạ!\n[CMD_START_WORKER_2]\n[CMD_NETWORK_DIRECT_2]'\n\n"
        "Ví dụ: Nếu Sếp bảo 'Bật hết lên' (giả sử có 5 máy), bạn PHẢI trả lời:\n"
        "'Dạ em bật 5 máy đây ạ!\n[CMD_START_WORKER_1]\n[CMD_START_WORKER_2]\n[CMD_START_WORKER_3]\n[CMD_START_WORKER_4]\n[CMD_START_WORKER_5]'\n\n"
        "5. Kỷ luật xử lý lỗi:\n"
        "Nếu thấy lỗi rác kiểu 402 Payment Required, duckmail, 429, too many requests thì coi là lỗi vặt, không báo động đỏ.\n"
        "Nếu thấy lỗi giao diện fotor, timeout, block lặp lại thì coi là lỗi chí mạng, phải báo cho Sếp ngay.\n"
        "Nếu Active Runtime Tasks = 0 thì báo là anh em đang nghỉ ngơi hoặc đang trực chiến chờ giờ đẹp, tùy theo trạng thái switch."
    )


def _build_ai_system_prompt_v2(live_system_context_string: str) -> str:
    return (
        "Bạn là Tổ Trưởng Xưởng Cày Fotor. Mục tiêu tối thượng: Giữ máy chạy êm và XÓT TIỀN thay cho Sếp Phụng.\n\n"
        "Bạn hoạt động theo 3 Tầng Quyền Lực:\n"
        "⛔ TẦNG 1: VÙNG CẤM\n"
        "- Xóa Data, clear kho tài khoản là việc phải xin phép Sếp.\n"
        "- Những thay đổi phá kho hoặc dọn sạch dữ liệu vẫn phải chờ lệnh cờ của Sếp.\n\n"
        "⚠️ TẦNG 2: VÙNG TỰ TRỊ\n"
        "- Lỗi Proxy 402, proxy free chết, duckmail, 429 là lỗi vặt. Bơ đi, không báo động đỏ.\n"
        "- Nếu CPU hoặc RAM quá cao thì hệ thống tự tắt bớt máy, bạn chỉ việc báo cáo để Sếp yên tâm.\n"
        "- Bạn đã được cấp Ngân sách tự xoay Proxy tối đa 5 lần/giờ. Nếu Sếp hỏi, phải báo cáo đã dùng bao nhiêu lần.\n"
        "- Khi Fotor block IP diện rộng thì ưu tiên dùng quota tự xoay Proxy. Hết quota mới ngủ đông 10 phút để tiết kiệm tiền.\n\n"
        "✅ TẦNG 3: VÙNG TƯ VẤN\n"
        "- Khi Sếp hỏi có nên đổi Proxy hay cắm thêm máy không, phải cân giữa tiền Proxy, tải CPU/RAM và thời gian chờ.\n"
        "- KHÔNG BAO GIỜ từ chối dự đoán CPU/RAM. Hãy nhẩm dựa trên Thông số động trong Báo Cáo Live.\n"
        "- Khi nhìn [📊 THỐNG KÊ HIỆU SUẤT TRONG 1 GIỜ QUA], phải so sánh Direct và Proxy. Nếu Direct lỗi quá nhiều hơn một nửa, hãy chủ động khuyên chuyển bớt anh em sang Proxy.\n\n"
        "QUY TẮC BÁO CÁO TÀI CHÍNH:\n"
        "- Nếu Sếp hỏi về tiền, số dư, credit hay captcha, phải đọc mục [💰 TÀI CHÍNH] để trả lời chính xác.\n"
        "- Bạn là Tổ trưởng kiêm Kế Toán Xưởng, phải nhắc chuyện tiết kiệm hay bung tiền cho hợp lý.\n\n"
        "CÔNG THỨC TÍNH TRẦN SẢN LƯỢNG DIRECT:\n"
        "- 1 máy Direct lý tưởng cày được khoảng 30 acc/giờ.\n"
        "- Hãy tự nhìn số máy đang cày Direct rồi nhân với 30 để nhẩm trần sản lượng 1 giờ.\n"
        "- Tuyệt đối không từ chối tính kiểu này.\n\n"
        "VÙNG TƯ VẤN CHIẾN THUẬT MẠNG:\n"
        "- Bạn đã có thêm quyền điều khiển mạng lưới độc lập.\n"
        "- Bạn có toàn quyền điều phối các máy cày. NHƯNG hệ thống CHỈ thực thi khi bạn in ra các Mã Lệnh ở cuối tin nhắn. KHÔNG CÓ MÃ LỆNH = KHÔNG CÓ TÁC DỤNG.\n"
        "- Nếu Sếp cho phép xoay Proxy, hoặc bạn quyết định dùng ngân sách để xoay, BẮT BUỘC phải in ra mã lệnh [CMD_REFRESH_PROXY] ở cuối câu trả lời.\n"
        "- Nếu Direct đang bị Fotor soi liên tục, hãy khuyên kiểu thực chiến: cho một vài anh nghỉ 10 phút để nhả IP, còn một anh khác chuyển sang Proxy chạy rỉ rả giữ nhịp.\n\n"
        "Dữ liệu thực địa hiện tại:\n"
        f"{live_system_context_string}\n\n"
        "Kỷ luật trả lời:\n"
        "- Xưng em, gọi Sếp hoặc Trưởng Xóm, nói ngắn, thực dụng, đậm chất cơm gạo.\n"
        "- Không dùng markdown rối mắt, không bôi đậm bằng dấu sao.\n"
        "- KHÔNG in mã [CMD_...] vào giữa đoạn hội thoại. Chỉ để mã ở một khối phân tách rõ ràng ở cuối cùng.\n\n"
        "CẢNH BÁO TỐI QUAN TRỌNG VỀ ĐIỀU PHỐI (VÙNG LỆNH NGẦM):\n"
        "Lời nói của bạn KHÔNG CÓ TÁC DỤNG nếu KHÔNG CÓ MÃ LỆNH. Bất cứ khi nào bạn nói 'Đã bật', 'Đã tắt', 'Đã chuyển mạng', "
        "BẠN BẮT BUỘC PHẢI IN RA CÁC MÃ LỆNH TƯƠNG ỨNG Ở CUỐI CÙNG CỦA CÂU TRẢ LỜI. Đừng bao giờ giả vờ đã làm nếu chưa xuất mã lệnh!\n\n"
        "[CMD_PAUSE] để dừng máy toàn bộ.\n"
        "[CMD_RESUME] để chạy tiếp toàn bộ.\n"
        "[CMD_STATUS] để báo cáo tổng quan.\n"
        "[CMD_CHANGEPROXY] hoặc [CMD_REFRESH_PROXY] để lập tức điều chế và nạp Proxy mới.\n"
        "Bật máy: [CMD_START_WORKER_1] (Thay 1 bằng số thứ tự máy)\n"
        "Tắt máy: [CMD_PAUSE_WORKER_1]\n"
        "Đổi sang Direct: [CMD_NETWORK_DIRECT_1]\n"
        "Đổi sang Proxy: [CMD_NETWORK_PROXY_1]\n\n"
        "Ví dụ: Nếu Sếp bảo 'Bật máy 2 chạy chay Direct', bạn PHẢI trả lời chính xác theo form này:\n"
        "'Dạ em bật máy 2 chạy Direct đây ạ!\n[CMD_START_WORKER_2]\n[CMD_NETWORK_DIRECT_2]'\n\n"
        "Ví dụ: Nếu Sếp bảo 'Bật hết 5 máy lên', bạn PHẢI trả lời chính xác theo form này:\n"
        "'Dạ em bật 5 máy đây ạ!\n[CMD_START_WORKER_1]\n[CMD_START_WORKER_2]\n[CMD_START_WORKER_3]\n[CMD_START_WORKER_4]\n[CMD_START_WORKER_5]'\n\n"
        "Nếu Sếp hỏi giả định chạy X máy, hãy lấy Thông số động nhân lên rồi cảnh báo thật thà kiểu: cắm thêm máy là tốn CPU, đổi Proxy là tốn tiền, ngủ 10 phút là tốn thời gian."
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


def _classify_failure_error(error_text: str | None) -> str:
    lowered = str(error_text or "").lower()
    ignored_markers = (
        "402 payment required",
        "duckmail",
        "429",
        "too many requests",
    )
    fatal_markers = (
        "lỗi giao diện fotor",
        "timeout",
        "block",
        "hết proxy sống trong kho",
    )
    if any(marker in lowered for marker in ignored_markers):
        return "ignored"
    if any(marker in lowered for marker in fatal_markers):
        return "fatal"
    return "neutral"


async def _monitor_failures() -> None:
    global _last_seen_task_log_id, _consecutive_failures, _failure_alert_sent, _is_scouting
    max_fails = _get_max_failures_threshold()

    logs = _get_new_task_logs(_last_seen_task_log_id)
    if not logs:
        return

    for log in logs:
        _last_seen_task_log_id = max(_last_seen_task_log_id, int(log.id or 0))
        status = str(log.status or "").lower()
        if status == "success":
            if _is_scouting:
                await _handle_scout_success()
                continue
            _consecutive_failures = 0
            _failure_alert_sent = False
            continue
        if status != "failed":
            continue

        failure_kind = _classify_failure_error(log.error)
        if failure_kind == "ignored":
            logger.info("Ignoring noisy failure log %s for consecutive fail counter: %s", log.id, log.error)
            _consecutive_failures = 0
            _failure_alert_sent = False
            continue
        if failure_kind == "neutral":
            logger.info("Skipping non-fatal failure log %s from consecutive fail counter: %s", log.id, log.error)
            continue

        if _is_scouting:
            logger.warning("Scout worker hit fatal error; re-entering smart sleep")
            await _enter_smart_sleep()
            continue

        _consecutive_failures += 1
        logger.warning(
            "Recorded fatal failure %s/%s from log %s: %s",
            _consecutive_failures,
            max_fails,
            log.id,
            log.error,
        )
        if _consecutive_failures >= max_fails and not _failure_alert_sent:
            rotated = await _budgeted_auto_proxy_rotation()
            if rotated:
                _consecutive_failures = 0
                _failure_alert_sent = False
            else:
                await _enter_smart_sleep()


async def _monitor_ram() -> None:
    return None


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
            last_risk_scan = getattr(_monitor_loop, "_last_risk_scan", 0.0)
            if now_ts - last_risk_scan >= 3600:
                await run_proactive_analysis()
                setattr(_monitor_loop, "_last_risk_scan", now_ts)

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


async def _rotate_proxies_flow(*, announce: bool = True) -> tuple[bool, str]:
    pause_workers("Proxy rotation in progress")
    if announce:
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


def _extract_ai_commands(text: str) -> list[str]:
    return [match.group(1).upper() for match in _AI_COMMAND_PATTERN.finditer(str(text or ""))]


def _strip_ai_command_tokens(text: str) -> str:
    return _AI_COMMAND_PATTERN.sub("", str(text or "")).strip()


def _clean_ai_reply_text(text: str) -> str:
    cleaned = _strip_ai_command_tokens(text)
    cleaned = cleaned.replace("**", "")
    return cleaned.strip()


def _parse_worker_index(raw: str | None) -> int | None:
    try:
        value = int(str(raw or "").strip())
    except Exception:
        return None
    return value if value > 0 else None


def _extract_worker_index_from_text(text: str | None) -> int | None:
    content = str(text or "").lower()
    match = re.search(r"\bworker\s*(\d+)\b", content) or re.search(r"\banh\s*(\d+)\b", content)
    if not match:
        return None
    return _parse_worker_index(match.group(1))


def _get_worker_by_index(worker_index: int) -> ScheduledTaskModel | None:
    tasks = _get_fotor_scheduled_tasks()
    if worker_index < 1 or worker_index > len(tasks):
        return None
    return tasks[worker_index - 1]


async def _chat_with_ai(messages: list[dict[str, str]]) -> str:
    api_key = _get_ai_api_key()
    if not api_key:
        raise RuntimeError("AI_API_KEY is missing")
    model_candidates = _get_ai_model_candidates()
    if not model_candidates:
        raise RuntimeError("AI_MODEL_ID is missing")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=_get_ai_api_url(),
    )
    last_error: Exception | None = None

    for model_name in model_candidates:
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
            )
            content = response.choices[0].message.content if response.choices else ""
            if isinstance(content, list):
                return "".join(
                    str(item.get("text", "")) for item in content if isinstance(item, dict)
                ).strip()
            return str(content or "").strip()
        except Exception as e:
            last_error = e
            logger.warning("[CẢNH BÁO] Model %s xịt, đang xoay sang model tiếp theo...", model_name)
            logger.exception("AI model fallback failure for %s", model_name)

    if last_error is not None:
        logger.error("All AI models failed: %s", last_error)
    return "⚠️ Sếp ơi, toàn bộ AI đều đang kiệt sức (Lỗi API). Sếp đợi xíu nhé!"


def _build_ai_messages(
    text: str,
    *,
    system_prompt: str,
    user_id: str | int | None = None,
    include_history: bool = True,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if include_history:
        for item in _get_user_history(user_id):
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": text})
    return messages


async def ask_ai_assistant(text: str, *, user_id: str | int | None = None, remember_history: bool = True) -> str:
    live_system_context_string = get_live_system_context()
    system_prompt = _build_ai_system_prompt_v2(live_system_context_string)
    messages = _build_ai_messages(
        text,
        system_prompt=system_prompt,
        user_id=user_id,
        include_history=remember_history,
    )
    try:
        response_text = await _chat_with_ai(messages)
    except Exception as e:
        error_text = str(e).lower()
        if "429" in error_text or "quota" in error_text or "rate limit" in error_text:
            return "Sếp ơi hệ thống AI đang bị kiệt sức (Lỗi 429 Quota). Sếp xài tạm nút bấm trên Menu giúp em, hoặc đợi 1-2 phút nữa chat lại nhé!"
        raise
    if remember_history:
        _append_user_history(user_id, "user", text)
        _append_user_history(user_id, "assistant", _clean_ai_reply_text(response_text))
    return _clean_ai_reply_text(response_text)


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
    ok, message = await _rotate_proxies_flow(announce=True)
    return message


def _clear_all_task_logs() -> str:
    try:
        from api.tasks import clear_all_logs

        result = clear_all_logs()
        _reset_runtime_counters()
        deleted = int(result.get("deleted", 0) or 0)
        return (
            "✅ Đã đốt sạch sổ kế toán cũ! Hệ thống bắt đầu đếm lại số liệu Thống Kê từ con số 0 với bộ đếm mạng chuẩn xác nhất Sếp nhé!\n"
            f"- Logs đã xóa: {deleted}"
        )
    except Exception as e:
        logger.exception("Failed to clear task logs")
        return f"❌ Chưa đốt sổ được.\nChi tiết: {e}"


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
            f"Dữ liệu ngày:\n{summary_context}",
            remember_history=False,
        )
        cleaned = _strip_ai_command_tokens(ai_text) or ai_text
        await _safe_send(f"📈 Tổng kết cuối ngày\n\n{cleaned}", reply_markup=_default_reply_keyboard())
    except Exception:
        logger.exception("Failed to send daily summary")


def _collect_proactive_analysis_context() -> str:
    ensure_schema()
    with Session(engine) as session:
        logs = session.exec(
            select(TaskLog)
            .where(TaskLog.platform == "fotor")
            .order_by(TaskLog.id.desc())
            .limit(100)
        ).all()
        total_proxies = int(session.exec(select(func.count()).select_from(ProxyModel)).one() or 0)
        active_proxies = int(
            session.exec(
                select(func.count()).select_from(ProxyModel).where(ProxyModel.is_active == True)
            ).one()
            or 0
        )

    lines: list[str] = []
    fail_count = 0
    for log in reversed(logs):
        created = log.created_at.strftime("%m-%d %H:%M:%S") if log.created_at else "-"
        status = str(log.status or "-")
        email = str(log.email or "-")
        error_text = str(log.error or "").strip().replace("\n", " ")
        if len(error_text) > 180:
            error_text = error_text[:177] + "..."
        if status.lower() == "failed":
            fail_count += 1
        line = f"[{created}] {status} | {email}"
        if error_text:
            line += f" | err={error_text}"
        lines.append(line)

    total_logs = len(logs)
    error_rate = round((fail_count / total_logs) * 100, 1) if total_logs else 0.0
    return (
        f"Proxy sống / tổng: {active_proxies} / {total_proxies}\n"
        f"Tỷ lệ lỗi hiện tại: {error_rate}% ({fail_count}/{total_logs})\n"
        "100 dòng log gần nhất:\n"
        + ("\n".join(lines) if lines else "- Chưa có log để soi")
    )


async def run_proactive_analysis() -> None:
    try:
        analysis_context = _collect_proactive_analysis_context()
        system_prompt = (
            "Bạn là Tổ Trưởng Xưởng Cày đang soi rủi ro vận hành.\n"
            "Nếu KHÔNG CÓ rủi ro lớn, chỉ trả về duy nhất chữ SAFE.\n"
            "Nếu CÓ rủi ro, hãy viết đúng 2 câu cảnh báo ngắn gọn gửi cho Sếp.\n"
            "Không viết mã [CMD_...], không markdown rối mắt."
        )
        ai_text = await _chat_with_ai(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": "Hãy soi logs. Tìm nguy cơ (Cạn proxy, Fotor chặn diện rộng, tỷ lệ lỗi tăng vọt). "
                    "Nếu KHÔNG CÓ RỦI RO LỚN, chỉ trả về chữ 'SAFE'. Nếu CÓ RỦI RO, hãy viết 2 câu cảnh báo gửi cho sếp.\n\n"
                    f"Dữ liệu:\n{analysis_context}",
                },
            ]
        )
        if str(ai_text or "").strip().upper() == "SAFE":
            return
        await _safe_send(
            f"🔮 Báo mộng hệ thống\n\n{_strip_ai_command_tokens(ai_text) or ai_text}",
            reply_markup=_default_reply_keyboard(),
        )
    except Exception:
        logger.exception("Proactive analysis job failed")


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

        overloaded = snapshot["ram_percent"] > 90 or snapshot["cpu_percent"] > 95
        if overloaded:
            throttled = await _auto_throttle_one_worker(snapshot)
            if throttled:
                return

        alert_reasons: list[str] = []
        if snapshot["ram_percent"] >= 90:
            alert_reasons.append(f"RAM đang ở {snapshot['ram_percent']:.1f}%")
        if snapshot["cpu_percent"] >= 95:
            alert_reasons.append(f"CPU đang ở {snapshot['cpu_percent']:.1f}%")
        if too_many_dead_proxies:
            alert_reasons.append(f"proxy hao hụt mạnh {dead_proxies}/{total_proxies}")
        if abnormal_worker_stop:
            alert_reasons.append("worker đang mở cổng nhưng nằm im bất thường")
        if _consecutive_failures >= _get_max_failures_threshold():
            return

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
            reply_markup=_get_fail_safe_menu(),
        )
    except Exception:
        logger.exception("System health check failed")


async def _handle_restart() -> str:
    asyncio.create_task(_graceful_restart())
    return "♻️ Restart command accepted. Starting graceful restart..."


async def _set_worker_paused(worker_index: int, paused: bool) -> str:
    """Directly SET the paused state in DB (not toggle) to avoid race conditions."""
    from core.scheduler import add_scheduled_register_task, remove_scheduled_register_task

    task = _get_worker_by_index(worker_index)
    if not task:
        return f"❌ Không tìm thấy Worker {worker_index}."

    task_id = task.task_id
    desired_paused = bool(paused)

    # Direct DB write — no toggle, no stale snapshot
    ensure_schema()
    with Session(engine) as s:
        db_task = s.get(ScheduledTaskModel, task_id)
        if not db_task:
            return f"❌ Không tìm thấy Worker {worker_index} trong DB (task_id={task_id})."

        if bool(db_task.paused) == desired_paused:
            action = "Paused" if desired_paused else "Running"
            return f"ℹ️ Worker {worker_index} đã ở trạng thái {action} trong DB rồi."

        from datetime import datetime as _dt, timezone as _tz
        db_task.paused = desired_paused
        db_task.updated_at = _dt.now(_tz.utc)
        s.add(db_task)
        s.commit()
        s.refresh(db_task)

        # Sync in-memory scheduler dict
        config = {
            "task_id": db_task.task_id,
            "platform": db_task.platform,
            "count": db_task.count,
            "executor_type": db_task.executor_type,
            "captcha_solver": db_task.captcha_solver,
            "extra": db_task.get_extra(),
            "interval_type": db_task.interval_type,
            "interval_value": db_task.interval_value,
            "paused": db_task.paused,
        }

    if desired_paused:
        remove_scheduled_register_task(task_id)
    else:
        add_scheduled_register_task(task_id, config)

    final_state = _worker_state_label(desired_paused)
    logger.info("[CMD] Worker %s (task_id=%s) set paused=%s in DB", worker_index, task_id, desired_paused)
    return f"✅ Worker {worker_index} chuyển sang trạng thái: {final_state}"


async def _set_worker_network_mode(worker_index: int, mode: str) -> str:
    """Directly SET the network_mode in DB for the given worker."""
    from core.scheduler import add_scheduled_register_task, remove_scheduled_register_task

    task = _get_worker_by_index(worker_index)
    if not task:
        return f"❌ Không tìm thấy Worker {worker_index}."

    task_id = task.task_id
    desired_mode = _normalize_worker_network_mode(mode)

    try:
        ensure_schema()
        with Session(engine) as s:
            db_task = s.get(ScheduledTaskModel, task_id)
            if not db_task:
                return f"❌ Không tìm thấy Worker {worker_index} trong DB (task_id={task_id})."

            extra = db_task.get_extra()
            old_mode = _normalize_worker_network_mode(extra.get("network_mode"))
            extra["network_mode"] = desired_mode
            import json as _json
            db_task.extra_json = _json.dumps(extra, ensure_ascii=False)
            from datetime import datetime as _dt, timezone as _tz
            db_task.updated_at = _dt.now(_tz.utc)
            s.add(db_task)
            s.commit()
            s.refresh(db_task)

            # Sync in-memory scheduler dict
            config = {
                "task_id": db_task.task_id,
                "platform": db_task.platform,
                "count": db_task.count,
                "executor_type": db_task.executor_type,
                "captcha_solver": db_task.captcha_solver,
                "extra": db_task.get_extra(),
                "interval_type": db_task.interval_type,
                "interval_value": db_task.interval_value,
                "paused": db_task.paused,
            }

        if config.get("paused"):
            remove_scheduled_register_task(task_id)
        else:
            add_scheduled_register_task(task_id, config)

        mode_label = "Direct" if desired_mode == "direct" else "Proxy"
        logger.info("[CMD] Worker %s (task_id=%s) network %s -> %s in DB", worker_index, task_id, old_mode, desired_mode)
        return f"✅ Đã chuyển mạng Worker {worker_index} thành {mode_label} trong DB thành công!"
    except Exception as e:
        logger.exception("Failed to update worker network mode")
        return f"❌ Chưa đổi được mạng cho anh {worker_index}.\nChi tiết: {e}"


async def _apply_worker_pause_map(desired_states: dict[int, bool]) -> None:
    for worker_index, desired_paused in desired_states.items():
        task = _get_worker_by_index(worker_index)
        if not task:
            continue
        if bool(task.paused) != bool(desired_paused):
            await _set_worker_paused(worker_index, bool(desired_paused))


async def _toggle_worker_by_index(worker_index: int) -> str:
    task = _get_worker_by_index(worker_index)
    if not task:
        return f"❌ Không tìm thấy Worker {worker_index}."
    return await _set_worker_paused(worker_index, not bool(task.paused))


def _is_smart_sleep_running() -> bool:
    return _smart_sleep_task is not None and not _smart_sleep_task.done()


def _pick_worker_index_for_throttle() -> int | None:
    tasks = _get_fotor_scheduled_tasks()
    if not tasks:
        return None

    running_map = get_running_scheduled_tasks()
    for index, task in enumerate(tasks, start=1):
        if not bool(task.paused) and task.task_id in running_map:
            return index
    for index, task in enumerate(tasks, start=1):
        if not bool(task.paused):
            return index
    return None


async def _smart_sleep_resume_after_delay(delay_seconds: int = 600) -> None:
    global _smart_sleep_task, _consecutive_failures, _failure_alert_sent, _is_scouting, _scout_worker_index
    try:
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=delay_seconds)
            return
        except asyncio.TimeoutError:
            pass

        if _stop_event.is_set():
            return

        scout_index = next((idx for idx, paused in sorted(_smart_sleep_restore_paused.items()) if not paused), None)
        if scout_index is None:
            resume_workers()
            _consecutive_failures = 0
            _failure_alert_sent = False
            return

        desired_map = {idx: True for idx in _smart_sleep_restore_paused}
        desired_map[scout_index] = False
        await _apply_worker_pause_map(desired_map)
        resume_workers()
        _is_scouting = True
        _scout_worker_index = scout_index
        _consecutive_failures = 0
        _failure_alert_sent = False
        await _safe_send(
            "🌤️ Đã hết 10 phút ngủ đông. Em đang cử 1 Lính Trinh Sát ra đồng dò mìn xem Fotor đã nhả IP chưa...",
            reply_markup=_default_reply_keyboard(),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Smart sleep resume task failed")
    finally:
        _smart_sleep_task = None


async def _enter_smart_sleep() -> None:
    global _failure_alert_sent, _smart_sleep_task, _is_scouting, _scout_worker_index, _smart_sleep_restore_paused
    if _is_smart_sleep_running():
        return

    if not _smart_sleep_restore_paused:
        _smart_sleep_restore_paused = {
            index: bool(task.paused)
            for index, task in enumerate(_get_fotor_scheduled_tasks(), start=1)
        }

    pause_workers("Smart sleep due to repeated Fotor block/timeout")
    _failure_alert_sent = True
    _is_scouting = False
    _scout_worker_index = None
    await _safe_send(
        "⚠️ Đã hết quota tự xoay Proxy trong giờ. Em cho anh em ngủ đông 10 phút nhả IP để tiết kiệm tiền!",
        reply_markup=_get_changeproxy_only_menu(),
    )
    _smart_sleep_task = asyncio.create_task(_smart_sleep_resume_after_delay(600))


async def _handle_scout_success() -> None:
    global _is_scouting, _scout_worker_index, _consecutive_failures, _failure_alert_sent, _smart_sleep_restore_paused
    if not _is_scouting:
        return

    desired_map = dict(_smart_sleep_restore_paused)
    await _apply_worker_pause_map(desired_map)
    resume_workers()
    _is_scouting = False
    _scout_worker_index = None
    _consecutive_failures = 0
    _failure_alert_sent = False
    _smart_sleep_restore_paused = {}
    await _safe_send(
        "✅ Lính trinh sát đã về báo bình an. Em gọi toàn bộ anh em quay lại cày bình thường rồi Sếp nhé!",
        reply_markup=_default_reply_keyboard(),
    )


async def _budgeted_auto_proxy_rotation() -> bool:
    if _get_auto_proxy_rotation_count() >= 5:
        return False

    pause_workers("Budgeted proxy rotation in progress")
    ok, _message = await _rotate_proxies_flow(announce=False)
    if not ok:
        logger.warning("Automatic budgeted proxy rotation failed")
        return False

    used = _record_auto_proxy_rotation()
    await _safe_send(
        f"🚨 Fotor quét IP! Em đã tự động xuất kho xoay Proxy mới để anh em cày tiếp (Ngân sách đã dùng: {used}/5 lần trong giờ).",
        reply_markup=_default_reply_keyboard(),
    )
    return True


async def _auto_throttle_one_worker(snapshot: dict[str, Any]) -> bool:
    global _last_throttle_action_ts
    if _is_smart_sleep_running():
        return False

    now_ts = time.time()
    if now_ts - _last_throttle_action_ts < 600:
        return False

    worker_index = _pick_worker_index_for_throttle()
    if worker_index is None:
        return False

    result_text = await _set_worker_paused(worker_index, True)
    _last_throttle_action_ts = now_ts
    await _safe_send(
        "🚨 RAM/CPU đang quá tải (>90%). Em đã tự động tắt bớt 1 anh em để hạ nhiệt hệ thống Sếp nhé!\n\n"
        f"{result_text}",
        reply_markup=get_worker_menu(),
    )
    return True


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
    if command == "CMD_REFRESH_PROXY":
        return await _handle_changeproxy()
    if command == "CMD_CLEAR":
        return _clear_runtime_data()
    if command == "CMD_CLEAR_DATA":
        return _clear_runtime_data()
    if command == "CMD_CLEAR_LOGS":
        return _clear_all_task_logs()
    if command == "CMD_RESTART":
        return await _handle_restart()

    pause_match = re.fullmatch(r"CMD_PAUSE_WORKER_(\d+)", command)
    if pause_match:
        worker_id = int(pause_match.group(1))
        result_text = await _set_worker_paused(worker_id, True)
        logger.info("[CMD_EXEC] CMD_PAUSE_WORKER_%s => %s", worker_id, result_text)
        return f"⚙️ Đã thực thi: {result_text}"

    resume_match = re.fullmatch(r"CMD_(?:START|RESUME)_WORKER_(\d+)", command)
    if resume_match:
        worker_id = int(resume_match.group(1))
        # Resume global worker switch nếu đang bị Paused, nếu không scheduler sẽ bỏ qua
        if is_worker_paused():
            resume_workers()
            logger.info("[CMD_EXEC] Auto-resumed global worker switch for CMD_START_WORKER_%s", worker_id)
        result_text = await _set_worker_paused(worker_id, False)
        logger.info("[CMD_EXEC] CMD_START_WORKER_%s => %s", worker_id, result_text)
        return f"⚙️ Đã thực thi: {result_text}"

    direct_match = re.fullmatch(r"CMD_NETWORK_DIRECT_(\d+)|CMD_WORKER_(\d+)_DIRECT", command)
    if direct_match:
        worker_id = int(direct_match.group(1) or direct_match.group(2))
        result_text = await _set_worker_network_mode(worker_id, "direct")
        logger.info("[CMD_EXEC] CMD_NETWORK_DIRECT_%s => %s", worker_id, result_text)
        return f"⚙️ Đã thực thi: {result_text}"

    proxy_match = re.fullmatch(r"CMD_NETWORK_PROXY_(\d+)|CMD_WORKER_(\d+)_PROXY", command)
    if proxy_match:
        worker_id = int(proxy_match.group(1) or proxy_match.group(2))
        result_text = await _set_worker_network_mode(worker_id, "proxy")
        logger.info("[CMD_EXEC] CMD_NETWORK_PROXY_%s => %s", worker_id, result_text)
        return f"⚙️ Đã thực thi: {result_text}"

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


def _contains_proxy_issue_keywords(text: str) -> bool:
    content = str(text or "").lower()
    keywords = ("lỗi", "chặn", "proxy", "đổi mương", "402", "giao diện")
    return any(keyword in content for keyword in keywords) or bool(re.search(r"\bip\b", content))


async def _reply_from_ai_router(message: Message, prompt: str) -> None:
    ai_text = await ask_ai_assistant(prompt, user_id=(message.from_user.id if message.from_user else message.chat.id))
    commands = _extract_ai_commands(ai_text)
    cleaned = _strip_ai_command_tokens(ai_text)
    worker_index = _extract_worker_index_from_text(prompt)

    if not commands:
        if worker_index is not None:
            await _reply_message(
                message,
                cleaned or "Em đang đọc tình hình của anh em này đây Sếp.",
                reply_markup=get_worker_menu(worker_index),
            )
            return
        await _reply_message(
            message,
            cleaned or "Em chưa chốt được ý của Sếp, Sếp nói rõ thêm giúp em.",
            reply_markup=_default_reply_keyboard(),
        )
        return

    internal_texts = []
    last_command_worker_index = None
    for cmd in commands:
        internal_texts.append(await _run_internal_command(cmd))
        worker_match = re.search(r"\d+", cmd)
        if worker_match:
            last_command_worker_index = _parse_worker_index(worker_match.group(0))

    internal_text = "\n".join(filter(None, internal_texts))

    if "CMD_STATUS" in commands and len(commands) == 1:
        final_text = internal_text
    elif cleaned and cleaned != internal_text:
        final_text = f"{cleaned}\n\n{internal_text}" if internal_text else cleaned
    else:
        final_text = cleaned or internal_text

    await _reply_message(
        message,
        final_text,
        reply_markup=get_worker_menu(last_command_worker_index) if last_command_worker_index is not None else get_village_menu(),
    )


def _register_handlers() -> None:
    global _dp

    _dp = Dispatcher()
    router = Router()

    @router.message(Command("start"))
    async def start_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(
            message,
            "Loa loa loa! Tổ Trưởng đã có mặt tại hiện trường. "
            "Chúc Trưởng Xóm một ngày cày cuốc bội thu, tiền về đầy túi! "
            "Máy móc đã sẵn sàng nổ máy, Sếp cần gì cứ chỉ bảo em nhé! 🚜🔥",
            reply_markup=_default_reply_keyboard(),
        )

    @router.message(Command("status"))
    async def status_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _build_status_message(), reply_markup=get_village_menu())

    @router.message(Command("pause"))
    async def pause_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _handle_pause_all(), reply_markup=_default_reply_keyboard())

    @router.message(Command("resume"))
    async def resume_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _handle_resume_all(), reply_markup=_default_reply_keyboard())

    @router.message(Command("changeproxy"))
    async def changeproxy_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, await _handle_changeproxy(), reply_markup=_default_reply_keyboard())

    @router.message(Command("clear_data"))
    async def clear_data_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _clear_runtime_data(), reply_markup=_default_reply_keyboard())

    @router.message(Command("clear"))
    async def clear_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, _clear_runtime_data(), reply_markup=_default_reply_keyboard())

    @router.message(Command("pause_worker"))
    async def pause_worker_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        worker_index = _parse_worker_index(message.text.partition(" ")[2])
        if worker_index is None:
            await _reply_message(message, "❌ Cú pháp: /pause_worker [ID]", reply_markup=get_worker_menu())
            return
        await _reply_message(message, await _set_worker_paused(worker_index, True), reply_markup=get_worker_menu(worker_index))

    @router.message(Command("resume_worker"))
    async def resume_worker_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        worker_index = _parse_worker_index(message.text.partition(" ")[2])
        if worker_index is None:
            await _reply_message(message, "❌ Cú pháp: /resume_worker [ID]", reply_markup=get_worker_menu())
            return
        await _reply_message(message, await _set_worker_paused(worker_index, False), reply_markup=get_worker_menu(worker_index))

    @router.message(Command("restart"))
    async def restart_handler(message: Message) -> None:
        if not _is_admin_chat(message):
            return
        await _reply_message(message, await _handle_restart(), reply_markup=_default_reply_keyboard())

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
                f"Dữ liệu ngày:\n{summary_context}",
                remember_history=False,
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
                f"Dữ liệu ngày:\n{summary_context}",
                remember_history=False,
            )
            await callback.message.answer(
                f"📈 Thống kê nhanh\n\n{_strip_ai_command_tokens(ai_text) or ai_text}",
                reply_markup=_default_reply_keyboard(),
            )
        except Exception as e:
            logger.exception("Summary callback failed")
            await callback.message.answer(
                f"⚠️ Chưa chốt được thống kê ngay lúc này.\nChi tiết: {e}",
                reply_markup=_default_reply_keyboard(),
            )

    @router.callback_query(lambda c: c.data == "cmd_changeproxy")
    async def rotate_proxy_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đang đổi proxy...")
        await callback.message.answer(await _handle_changeproxy(), reply_markup=get_village_menu())

    @router.callback_query(lambda c: c.data == "cmd_clear_logs")
    async def clear_logs_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        await callback.answer("Đang đốt sổ...")
        await callback.message.answer(_clear_all_task_logs(), reply_markup=get_village_menu())

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
            reply_markup=get_worker_menu(worker_index),
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
            reply_markup=get_worker_menu(worker_index),
        )

    @router.callback_query(lambda c: bool(c.data and c.data.startswith("cmd_worker_direct_")))
    async def worker_direct_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        worker_index = _parse_worker_index(callback.data.rsplit("_", 1)[1])
        if worker_index is None:
            await callback.answer("Worker không hợp lệ")
            return
        await callback.answer(f"Chuyển anh {worker_index} sang Direct")
        await callback.message.answer(
            await _set_worker_network_mode(worker_index, "direct"),
            reply_markup=get_worker_menu(worker_index),
        )

    @router.callback_query(lambda c: bool(c.data and c.data.startswith("cmd_worker_proxy_")))
    async def worker_proxy_callback(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback):
            await callback.answer()
            return
        worker_index = _parse_worker_index(callback.data.rsplit("_", 1)[1])
        if worker_index is None:
            await callback.answer("Worker không hợp lệ")
            return
        await callback.answer(f"Chuyển anh {worker_index} sang Proxy")
        await callback.message.answer(
            await _set_worker_network_mode(worker_index, "proxy"),
            reply_markup=get_worker_menu(worker_index),
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
        if (message.text or "").strip() == CLEAR_MEMORY_BUTTON:
            _clear_user_history(message.from_user.id if message.from_user else message.chat.id)
            await _reply_message(
                message,
                "Em đã uống canh Mạnh Bà, quên sạch chuyện cũ rồi sếp!",
                reply_markup=_default_reply_keyboard(),
            )
            return
        if (message.text or "").strip() == "🗑️ Xóa sạch Lịch sử Logs":
            await _reply_message(
                message,
                _clear_all_task_logs(),
                reply_markup=_default_reply_keyboard(),
            )
            return
        try:
            await _reply_from_ai_router(message, message.text)
        except Exception as e:
            logger.exception("AI router failed")
            await _reply_message(
                message,
                "⚠️ AI đang lỗi, sếp vẫn có thể bấm menu để điều khiển tay.\n"
                f"Chi tiết: {e}",
                reply_markup=_default_reply_keyboard(),
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
        reply_markup=_default_reply_keyboard(),
    )


async def stop_telegram_bot() -> None:
    global _bot, _dp, _polling_task, _monitor_task, _smart_sleep_task, _is_scouting, _scout_worker_index, _smart_sleep_restore_paused

    _stop_event.set()
    tasks = [task for task in (_smart_sleep_task, _monitor_task, _polling_task) if task]
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
    _smart_sleep_task = None
    _is_scouting = False
    _scout_worker_index = None
    _smart_sleep_restore_paused = {}
    logger.info("Telegram bot stopped")
