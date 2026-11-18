"""Resolve the FFmpeg executable bundled beside the Atlas installation."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from app_paths import get_install_dir, is_frozen


def bundled_ffmpeg_path() -> Path:
    return get_install_dir() / "ffmpeg" / "ffmpeg.exe"


def find_ffmpeg() -> Optional[str]:
    """Prefer the install-local FFmpeg; use PATH only for source development."""
    bundled = bundled_ffmpeg_path()
    if bundled.is_file():
        return str(bundled.resolve())
    if is_frozen():
        return None
    return shutil.which("ffmpeg")


def require_ffmpeg() -> str:
    executable = find_ffmpeg()
    if executable:
        return executable
    raise FileNotFoundError(
        f"Bundled FFmpeg was not found at the required location: {bundled_ffmpeg_path()}"
    )
