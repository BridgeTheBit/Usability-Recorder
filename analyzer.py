from __future__ import annotations

from asyncio import events
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


class SessionAnalyzer:
    """
    Analyzes recorded usability session files (events.csv and session_metadata.json).
    Calculates task duration, click counts, click rates, keystroke counts, and mouse movement statistics.
    """

    def __init__(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)
        self.metadata_path = self.session_dir / "session_metadata.json"
        self.events_path = self.session_dir / "events.csv"

    def analyze(self) -> Dict[str, Any]:
        """Perform analysis on the session directory and save analysis.json."""
        if not self.session_dir.exists():
            raise FileNotFoundError(f"Session directory not found: {self.session_dir}")

        metadata = self._load_metadata()
        events = self._load_events()


        # Calculate Task Duration
        task_duration_s = 0.0
        if events:
            timestamps = [e["timestamp_s"] for e in events if "timestamp_s" in e]
            if timestamps:
                task_duration_s = max(timestamps)

        # Fallback to metadata ISO timestamps if event duration is minimal
        if task_duration_s <= 0.0 and metadata.get("session_start_wall_clock") and metadata.get("session_end_wall_clock"):
            from datetime import datetime
            try:
                t_start = datetime.fromisoformat(metadata["session_start_wall_clock"])
                t_end = datetime.fromisoformat(metadata["session_end_wall_clock"])
                task_duration_s = (t_end - t_start).total_seconds()
            except Exception:
                pass

        mins = int(task_duration_s // 60)
        secs = task_duration_s % 60
        duration_formatted = f"{mins:02d}:{secs:05.2f}"

        # Event Counts & Aggregations
        total_clicks = 0
        left_clicks = 0
        right_clicks = 0
        middle_clicks = 0
        other_clicks = 0
        mouse_scrolls_up = 0
        mouse_scrolls_down = 0

        total_keystrokes = 0
        mouse_moves = 0
        mouse_scrolls = 0
        total_distance_px = 0.0

        last_x: Optional[float] = None
        last_y: Optional[float] = None

        for event in events:
            etype = event.get("event_type")
            detail = event.get("detail", {})

            if etype == "mouse_click":
                # Only count press actions to avoid double-counting press + release as 2 clicks
                if detail.get("pressed", True):
                    total_clicks += 1
                    btn = str(detail.get("button", "")).lower()
                    if "left" in btn:
                        left_clicks += 1
                    elif "right" in btn:
                        right_clicks += 1
                    elif "middle" in btn:
                        middle_clicks += 1
                    else:
                        other_clicks += 1

            elif etype == "key_press":
                total_keystrokes += 1

            elif etype == "mouse_move":
                mouse_moves += 1
                x = detail.get("x")
                y = detail.get("y")
                if x is not None and y is not None and last_x is not None and last_y is not None:
                    dx = float(x) - last_x
                    dy = float(y) - last_y
                    total_distance_px += math.sqrt(dx * dx + dy * dy)
                if x is not None and y is not None:
                    last_x = float(x)
                    last_y = float(y)

            elif etype == "mouse_scroll":
                mouse_scrolls += 1
                scroll_direction = detail.get("dy")
                if  scroll_direction == 1 or scroll_direction == "up":
                    mouse_scrolls_up += 1
                elif scroll_direction == -1 or scroll_direction == "down":
                    mouse_scrolls_down += 1

        # Clicks per minute
        duration_minutes = max(task_duration_s / 60.0, 0.001)
        clicks_per_minute = round(total_clicks / duration_minutes, 2) if task_duration_s > 0 else 0.0

        pupil_info = metadata.get("pupil_trigger_details", {})
        pupil_connected = bool(metadata.get("device_serial_number") or pupil_info.get("recording_started"))

        analysis_results = {
            "session_folder": self.session_dir.name,
            "participant_id": metadata.get("participant_id", "N/A"),
            "task_label": metadata.get("task_label", ""),
            "session": metadata.get("session", ""),
            "task_duration_seconds": round(task_duration_s, 3),
            "task_duration_formatted": duration_formatted,
            "total_clicks": total_clicks,
            "left_clicks": left_clicks,
            "right_clicks": right_clicks,
            "middle_clicks": middle_clicks,
            "clicks_per_minute": clicks_per_minute,
            "total_keystrokes": total_keystrokes,
            "mouse_moves_count": mouse_moves,
            "mouse_scrolls_count": mouse_scrolls,
            "mouse_scrolls_up": mouse_scrolls_up,
            "mouse_scrolls_down": mouse_scrolls_down,
            "estimated_mouse_distance_px": round(total_distance_px, 1),
            "pupil_connected": pupil_connected,
            "pupil_mode": metadata.get("pupil_mode", "unknown"),
            "fps": metadata.get("fps", 15),
            "privacy_mode": not metadata.get("full_key_logging", True),
        }

        self._save_analysis_json(analysis_results)
        return analysis_results

    def _load_metadata(self) -> Dict[str, Any]:
        if not self.metadata_path.exists():
            return {}
        try:
            with self.metadata_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_events(self) -> List[Dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events = []
        try:
            with self.events_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ts = float(row.get("timestamp_s", 0))
                        etype = row.get("event_type", "")
                        detail_str = row.get("detail", "{}")
                        detail = json.loads(detail_str) if detail_str else {}
                        events.append({
                            "timestamp_s": ts,
                            "event_type": etype,
                            "detail": detail,
                        })
                    except Exception:
                        continue
        except Exception:
            pass
        return events

    def _save_analysis_json(self, results: Dict[str, Any]) -> None:
        output_path = self.session_dir / "analysis.json"
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)


def batch_analyze_sessions(
    sessions_root: str | Path = "sessions",
    output_csv: str | Path = "sessions_summary.csv",
) -> Path:
    """
    Scans all session directories under sessions_root, calculates metrics for each,
    and exports a consolidated CSV report.
    """
    root = Path(sessions_root)
    output_path = Path(output_csv)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)

    session_dirs = [d for d in root.iterdir() if d.is_dir()]
    records = []

    for sdir in session_dirs:
        try:
            analyzer = SessionAnalyzer(sdir)
            res = analyzer.analyze()
            records.append(res)
        except Exception as exc:
            print(f"Warning: Failed to analyze session {sdir.name}: {exc}")

    fieldnames = [
        "participant_id",
        "task_label",
        "session_label",
        "task_duration_seconds",
        "task_duration_formatted",
        "total_clicks",
        "left_clicks",
        "right_clicks",
        "middle_clicks",
        "clicks_per_minute",
        "total_keystrokes",
        "estimated_mouse_distance_px",
        "mouse_moves_count",
        "mouse_scrolls_count",
        "pupil_connected",
        "session_folder",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)

    return output_path.resolve()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Usability Testing Recorder Metrics Analyzer")
    parser.add_argument("--sessions-dir", default="sessions", help="Path to sessions root folder")
    parser.add_argument("--output-csv", default="sessions_summary.csv", help="Path to output summary CSV file")
    args = parser.parse_argument_list() if hasattr(parser, "parse_argument_list") else parser.parse_args()

    csv_file = batch_analyze_sessions(args.sessions_dir, args.output_csv)
    print(f"[Analyzer] Successfully generated consolidated metrics CSV: {csv_file}")
