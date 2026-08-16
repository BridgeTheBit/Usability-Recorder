from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import ctypes
from ctypes import wintypes


# Windows API helpers for retrieving foreground window info
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


def _get_foreground_window_info() -> Dict[str, Any]:
    """
    Return a dict with the current foreground window's handle, title,
    and owning process name (best-effort).
    """
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "title": "", "process_name": ""}

    # Window title
    length = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value

    # Process name (best-effort; requires psutil)
    process_name = ""
    try:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            import psutil
            proc = psutil.Process(pid.value)
            process_name = proc.name()
    except Exception:
        pass

    return {"hwnd": hwnd, "title": title, "process_name": process_name}


class WindowTracker:
    """
    Background tracker that monitors the active (foreground) window.
    Each time the foreground window changes, a record is written to
    ``window_switches.csv`` with the timestamp relative to the shared
    session reference clock ``t0``.

    The tracker runs in its own daemon thread and polls at a configurable
    rate (default 4 Hz — sufficient for human window-switch speed while
    keeping CPU usage negligible).
    """

    def __init__(
        self,
        output_path: str,
        poll_rate_hz: float = 4.0,
    ) -> None:
        self.output_path = Path(output_path)
        self.poll_interval = 1.0 / max(poll_rate_hz, 0.5)
        self.t0: Optional[float] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Live state
        self._current_title: Optional[str] = None
        self._current_process: Optional[str] = None
        self._switch_count: int = 0
        self._switches: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Public API (mirrors InputLogger / ScreenRecorder patterns)
    # ------------------------------------------------------------------ #

    def start(self, t0: float) -> None:
        """Start tracking window switches relative to session reference t0."""
        self.t0 = t0
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._switch_count = 0
        self._switches = []
        self._current_title = None
        self._current_process = None

        self._write_header()

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> Dict[str, Any]:
        """Stop tracking and return summary statistics."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

        return self.get_summary()

    def get_summary(self) -> Dict[str, Any]:
        """Return aggregate window-switch statistics for the session."""
        unique_windows = set()
        for sw in self._switches:
            key = sw.get("process_name") or sw.get("title", "")
            if key:
                unique_windows.add(key)

        return {
            "total_window_switches": self._switch_count,
            "unique_windows_visited": len(unique_windows),
            "unique_window_names": sorted(unique_windows),
            "switches": self._switches,
        }

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _timestamp(self) -> float:
        """Monotonic seconds elapsed since session start t0."""
        if self.t0 is None:
            return 0.0
        return time.monotonic() - self.t0

    def _write_header(self) -> None:
        with self.output_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_s",
                "switch_index",
                "window_title",
                "process_name",
            ])

    def _log_switch(self, title: str, process_name: str) -> None:
        self._switch_count += 1
        ts = self._timestamp()
        record = {
            "timestamp_s": round(ts, 6),
            "switch_index": self._switch_count,
            "title": title,
            "process_name": process_name,
        }
        self._switches.append(record)

        with self._lock:
            with self.output_path.open("a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    f"{ts:.6f}",
                    self._switch_count,
                    title,
                    process_name,
                ])

    def _poll_loop(self) -> None:
        """Main polling loop — runs in a daemon thread."""
        # Record the initial foreground window as the first entry (switch_index=0 style)
        try:
            info = _get_foreground_window_info()
            self._current_title = info["title"]
            self._current_process = info["process_name"]
            # Log the initial window (counts as switch #1)
            self._log_switch(self._current_title, self._current_process)
        except Exception:
            pass

        while not self._stop_event.wait(self.poll_interval):
            try:
                info = _get_foreground_window_info()
                new_title = info["title"]
                new_process = info["process_name"]

                # Detect change: title OR process changed
                if new_title != self._current_title or new_process != self._current_process:
                    # Ignore transient blank titles (e.g., during Alt-Tab animation)
                    if new_title or new_process:
                        self._current_title = new_title
                        self._current_process = new_process
                        self._log_switch(new_title, new_process)
            except Exception:
                pass
