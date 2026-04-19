from __future__ import annotations

from datetime import datetime, timezone
import threading


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_lock = threading.Lock()
_state = {
    "paused": False,
    "reason": "",
    "updated_at": _utcnow_iso(),
}


def pause_workers(reason: str = "") -> dict:
    with _lock:
        _state["paused"] = True
        _state["reason"] = str(reason or "").strip()
        _state["updated_at"] = _utcnow_iso()
        return dict(_state)


def resume_workers() -> dict:
    with _lock:
        _state["paused"] = False
        _state["reason"] = ""
        _state["updated_at"] = _utcnow_iso()
        return dict(_state)


def is_worker_paused() -> bool:
    with _lock:
        return bool(_state["paused"])


def get_worker_state() -> dict:
    with _lock:
        return dict(_state)
