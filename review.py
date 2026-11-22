"""Read bounded, video-aligned events for session review."""

import csv
import json
from pathlib import Path


def review_events(session_dir: Path, limit: int = 2000) -> list[dict]:
    metadata_path = session_dir / "session_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    offset = float(metadata.get("screen_first_frame_timestamp_s") or 0)
    if "screen_capture_backend" not in metadata and 0 < offset < 0.1:
        offset = 0.35
    result = []
    with (session_dir / "events.csv").open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            if len(result) >= limit:
                break
            kind = row.get("event_type", "")
            if kind not in ("mouse_click", "mouse_scroll", "key_press"):
                continue
            try:
                detail = json.loads(row.get("detail") or "{}")
                seconds = max(0.0, float(row["timestamp_s"]) - offset)
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if kind == "mouse_click" and not detail.get("pressed", True):
                continue
            result.append({"time": round(seconds, 3), "type": kind,
                           "button": str(detail.get("button", "")) if kind == "mouse_click" else ""})
    return result
