"""Application paths that work from source and from a packaged Windows build."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_NAME = "Atlas"
PROJECT_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_install_dir() -> Path:
    """Return the executable folder for installed builds or the project root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return PROJECT_DIR


def get_legacy_app_data_dir() -> Path:
    """Return the per-user data folder used by Atlas versions before 1.2.2."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_NAME


def get_app_data_dir() -> Path:
    """Return data storage beside the installed application."""
    if not is_frozen():
        return PROJECT_DIR

    data_dir = get_install_dir() / "data"
    legacy_config = get_legacy_app_data_dir() / "config.json"
    config_path = data_dir / "config.json"
    if not config_path.exists() and legacy_config.is_file():
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_config, config_path)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Keep existing analysis exports available after moving from the old
    # per-user data location to the install-local data folder.
    legacy_summary = get_legacy_app_data_dir() / "sessions_summary.csv"
    summary_path = data_dir / legacy_summary.name
    if not summary_path.exists() and legacy_summary.is_file():
        shutil.copy2(legacy_summary, summary_path)
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
