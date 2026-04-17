"""Runtime paths with Hugging Face persistent storage fallback."""

from __future__ import annotations

import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent.parent
PERSISTENT_ROOT = Path("/data")


def get_runtime_root() -> Path:
    if os.path.exists(str(PERSISTENT_ROOT)):
        return PERSISTENT_ROOT
    return APP_ROOT


def get_runtime_file(filename: str) -> Path:
    root = get_runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


def get_runtime_logs_dir() -> Path:
    logs_dir = get_runtime_root() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir

