"""Read bounded, video-aligned events for session review."""

import csv
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import uuid

from ffmpeg_utils import require_ffmpeg

_conversion_lock = threading.Lock()
_conversions: dict[str, dict] = {}


def preview_status(session_dir: Path) -> dict:
    source = session_dir / "screen_recording.mp4"
    target = session_dir / "review_preview.webm"
    if not source.is_file():
        return {"status": "missing"}
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return {"status": "ready"}
    key = str(session_dir.resolve())
    with _conversion_lock:
        current = _conversions.get(key)
        if current and current["status"] in ("processing", "error") and current.get("source_mtime") == source.stat().st_mtime:
            return current.copy()
        _conversions[key] = {"status": "processing", "source_mtime": source.stat().st_mtime}
    threading.Thread(target=_convert_preview, args=(session_dir, key), daemon=True).start()
    return {"status": "processing"}


def _convert_preview(session_dir: Path, key: str) -> None:
    target = session_dir / "review_preview.webm"
    temporary = session_dir / f"review_preview-{uuid.uuid4().hex}.webm"
    try:
        command = [require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(session_dir / "screen_recording.mp4"), "-vf",
                   "scale=min(1280\\,iw):-2", "-c:v", "libvpx", "-deadline", "realtime",
                   "-cpu-used", "5", "-b:v", "1500k", "-c:a", "libopus", str(temporary)]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        completed = subprocess.run(command, capture_output=True, text=True,
                                   creationflags=flags, timeout=3600)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr[-500:] or "FFmpeg conversion failed")
        temporary.replace(target)
        result = {"status": "ready"}
    except Exception as error:
        result = {"status": "error", "error": str(error)}
    finally:
        temporary.unlink(missing_ok=True)
    with _conversion_lock:
        result["source_mtime"] = (session_dir / "screen_recording.mp4").stat().st_mtime
        _conversions[key] = result


def load_notes(session_dir: Path) -> list[dict]:
    path = session_dir / "review_notes.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def save_notes(session_dir: Path, notes: list[dict]) -> None:
    path = session_dir / "review_notes.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _iter_review_events(session_dir: Path):
    metadata_path = session_dir / "session_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    offset = float(metadata.get("screen_first_frame_timestamp_s") or 0)
    if "screen_capture_backend" not in metadata and 0 < offset < 0.1:
        offset = 0.35
    with (session_dir / "events.csv").open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            kind = row.get("event_type", "")
            if kind not in ("mouse_click", "mouse_scroll", "key_press"):
                continue
            try:
                detail = json.loads(row.get("detail") or "{}")
                seconds = float(row["timestamp_s"]) - offset
                if not isinstance(detail, dict) or not math.isfinite(seconds) or seconds < 0:
                    continue
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if kind == "mouse_click" and not detail.get("pressed", True):
                continue
            event = {"time": round(seconds, 3), "type": kind,
                     "button": str(detail.get("button", "")) if kind == "mouse_click" else ""}
            if kind == "mouse_click":
                try:
                    x, y = float(detail["x"]), float(detail["y"])
                    if math.isfinite(x) and math.isfinite(y):
                        event.update(x=x, y=y)
                except (KeyError, TypeError, ValueError):
                    pass
            yield event


def review_events_page(session_dir: Path, cursor: int = 0, limit: int = 2000) -> dict:
    """Page across valid, privacy-filtered events without silently truncating a session."""
    from itertools import islice
    if cursor < 0 or not 1 <= limit <= 5000:
        raise ValueError("Invalid review page")
    events = list(islice(_iter_review_events(session_dir), cursor, cursor + limit + 1))
    has_more = len(events) > limit
    return {"events": events[:limit], "next_cursor": cursor + limit if has_more else None}


def review_events(session_dir: Path, limit: int | None = None) -> list[dict]:
    from itertools import islice
    return list(islice(_iter_review_events(session_dir), limit))
