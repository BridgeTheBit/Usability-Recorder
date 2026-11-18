"""Application paths that work from source and from a packaged Windows build."""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Atlas"
PROJECT_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_app_data_dir() -> Path:
    """Return the writable per-user data directory for the current install."""
    if not is_frozen():
        return PROJECT_DIR
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    data_dir = base / APP_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_config_path() -> Path:
    return get_app_data_dir() / "config.json"


def get_default_sessions_dir() -> Path:
    return get_app_data_dir() / "sessions"


def resolve_user_path(value: str | Path) -> Path:
    """Resolve relative user-selected paths against the writable data directory."""
    if value is None or not str(value).strip():
        return get_default_sessions_dir()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = get_app_data_dir() / path
    return path.resolve()
