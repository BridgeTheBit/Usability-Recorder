from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from typing import Optional

from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse


class InputLogger:
    """
    Background input logger for mouse and keyboard events.
    All events are timestamped using Python's time.monotonic() clock relative to t0.
    """

    VALID_EVENT_TYPES = {
        "mouse_move",
        "mouse_click",
        "mouse_scroll",
        "key_press",
        "key_release",
    }

    def __init__(
        self,
        output_path: str,
        full_key_logging: bool = True,
        mouse_move_rate_hz: float = 30.0,
    ) -> None:
        if mouse_move_rate_hz <= 0:
            raise ValueError("Mouse movement sampling rate must be greater than zero.")
        self.output_path = Path(output_path)
        self.full_key_logging = full_key_logging
        self.mouse_move_rate_hz = mouse_move_rate_hz
        self.t0: Optional[float] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._mouse_listener: Optional[pynput_mouse.Listener] = None
        self._keyboard_listener: Optional[pynput_keyboard.Listener] = None
        self._thread: Optional[threading.Thread] = None
        self._last_move_time = 0.0
        self._key_press_count = 0
        self._key_release_count = 0
        self._error: Optional[Exception] = None

    def start(self, t0: float) -> None:
        """Start capturing mouse and keyboard events relative to session reference t0."""
        self.t0 = t0
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._last_move_time = 0.0
        self._key_press_count = 0
        self._key_release_count = 0
        self._error = None
        self._write_header()

        self._mouse_listener = pynput_mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
        )
        self._keyboard_listener = pynput_keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )

        try:
            self._mouse_listener.start()
            self._keyboard_listener.start()
            self._thread = threading.Thread(target=self._wait_for_stop, daemon=True)
            self._thread.start()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        """Stop the logger thread and detach background input listeners."""
        self._stop_event.set()
        errors = []
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is None:
                continue
            try:
                listener.stop()
            except Exception as exc:
                errors.append(str(exc))
            try:
                if listener.is_alive():
                    listener.join(timeout=2.0)
                    if listener.is_alive():
                        errors.append("An input listener did not stop within 2 seconds.")
            except Exception as exc:
                errors.append(str(exc))
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                errors.append("The input logger thread did not stop within 2 seconds.")
            self._thread = None
        self._mouse_listener = None
        self._keyboard_listener = None
        if self._error is not None:
            errors.append(f"Input log write failed: {self._error}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def _wait_for_stop(self) -> None:
        while not self._stop_event.wait(0.05):
            continue

    def _write_header(self) -> None:
        with self.output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp_s", "event_type", "detail"])

    def _timestamp(self) -> float:
        """
        SHARED REFERENCE CLOCK SYNCHRONIZATION:
        Calculates monotonic seconds elapsed since session start t0.
        Using time.monotonic() prevents drift caused by system wall-clock adjustments or NTP sync.
        """
        if self.t0 is None:
            return 0.0
        return time.monotonic() - self.t0

    def _log_event(self, event_type: str, detail: dict) -> None:
        if event_type not in self.VALID_EVENT_TYPES:
            return
        with self._lock:
            try:
                with self.output_path.open("a", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow([
                        f"{self._timestamp():.6f}",
                        event_type,
                        json.dumps(detail, ensure_ascii=True),
                    ])
            except Exception as exc:
                self._error = exc
                self._stop_event.set()
                for listener in (self._keyboard_listener, self._mouse_listener):
                    if listener is not None:
                        try:
                            listener.stop()
                        except Exception:
                            pass
                raise RuntimeError(f"Could not write {event_type} to {self.output_path}.") from exc

    def _on_mouse_move(self, x: int, y: int) -> None:
        elapsed = time.monotonic() - self._last_move_time
        if elapsed < (1.0 / self.mouse_move_rate_hz):
            return
        self._last_move_time = time.monotonic()
        self._log_event("mouse_move", {"x": x, "y": y})

    def _on_mouse_click(self, x: int, y: int, button, pressed: bool) -> None:
        self._log_event(
            "mouse_click",
            {
                "x": x,
                "y": y,
                "button": str(button),
                "pressed": pressed,
            },
        )

    def _on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._log_event("mouse_scroll", {"x": x, "y": y, "dx": dx, "dy": dy})

    def _on_key_press(self, key) -> None:
        if self.full_key_logging:
            detail = {"key": str(key)}
        else:
            self._key_press_count += 1
            detail = {"keystroke_count": self._key_press_count, "privacy_mode": True}
        self._log_event("key_press", detail)

    def _on_key_release(self, key) -> None:
        if self.full_key_logging:
            detail = {"key": str(key)}
        else:
            self._key_release_count += 1
            detail = {"keystroke_count": self._key_release_count, "privacy_mode": True}
        self._log_event("key_release", detail)
