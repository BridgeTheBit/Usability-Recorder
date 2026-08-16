from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
from pynput.mouse import Controller as MouseController

try:
    import cv2
    import mss
except Exception:  # pragma: no cover
    cv2 = None
    mss = None


class ScreenRecorder:
    """
    Screen recorder module.
    Prefers ffmpeg via subprocess for low CPU overhead and MP4 stability.
    Falls back to mss + OpenCV VideoWriter if ffmpeg is not available or fails.
    Handles headless/background execution scenarios gracefully by writing fallback frames if graphics capture is restricted.
    """

    def __init__(self, output_path: str, fps: int = 15) -> None:
        self.output_path = Path(output_path)
        self.fps = fps
        self.t0: Optional[float] = None
        self.ffmpeg_path: Optional[str] = self._find_ffmpeg()
        self._process: Optional[subprocess.Popen] = None
        self._mss = None
        self._writer = None
        self._monitor = None
        self._fallback_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._first_frame_timestamp_s: Optional[float] = None
        self._using_ffmpeg = False
        self._mouse_controller = MouseController()
        self._cursor_trail = []

    def _find_ffmpeg(self) -> Optional[str]:
        """Locate ffmpeg executable in system PATH or local project directory."""
        which_path = shutil.which("ffmpeg")
        if which_path:
            return which_path

        candidates = [
            Path("ffmpeg/bin/ffmpeg.exe"),
            Path("ffmpeg/ffmpeg.exe"),
            Path("ffmpeg/bin/ffmpeg"),
            Path("ffmpeg/ffmpeg"),
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate.resolve())

        return None

    def start(self, t0: float) -> None:
        """Start screen recording aligned with session monotonic clock t0."""
        self.t0 = t0
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        # Preliminary timestamp — will be updated below once we know the true start
        self._first_frame_timestamp_s = time.monotonic() - self.t0

        ffmpeg_success = False
        if self.ffmpeg_path:
            print(f"[ScreenRecorder] Attempting ffmpeg recording using: {self.ffmpeg_path}")
            try:
                self._start_ffmpeg_recording()
                # Check if ffmpeg exited immediately due to GDI error or permission issue
                time.sleep(0.3)
                if self._process is not None and self._process.poll() is None:
                    self._using_ffmpeg = True
                    ffmpeg_success = True
                    # Update timestamp AFTER confirming ffmpeg is running — this captures
                    # the true recording-start offset including ffmpeg initialization.
                    self._first_frame_timestamp_s = time.monotonic() - self.t0
                    print(f"[ScreenRecorder] ffmpeg started. first_frame_timestamp_s={self._first_frame_timestamp_s:.4f}s")
                else:
                    exit_code = self._process.poll() if self._process else "unknown"
                    print(f"[ScreenRecorder] ffmpeg exited immediately (code {exit_code}). Falling back to mss/OpenCV.")
                    self._process = None
            except Exception as exc:
                print(f"[ScreenRecorder] ffmpeg startup error: {exc}. Falling back to mss/OpenCV.")
                self._process = None

        if not ffmpeg_success:
            if mss is not None and cv2 is not None:
                print("[ScreenRecorder] Using mss + OpenCV fallback.")
                self._using_ffmpeg = False
                # Set timestamp right before capturing starts (mss starts immediately)
                self._first_frame_timestamp_s = time.monotonic() - self.t0
                self._start_mss_fallback()
            else:
                raise RuntimeError(
                    "Neither ffmpeg nor the mss/OpenCV fallback is available.\n"
                    "Please install ffmpeg on PATH or ensure mss and opencv-python are installed."
                )

    def stop(self) -> None:
        """Gracefully stop recording and finalize video container header (MOOV atom)."""
        self._stop_event.set()

        if self._process is not None:
            try:
                if self._using_ffmpeg:
                    if self._process.stdin:
                        try:
                            self._process.stdin.write(b"q\n")
                            self._process.stdin.flush()
                        except Exception:
                            pass

                    try:
                        self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        if os.name == "nt":
                            try:
                                self._process.send_signal(signal.CTRL_BREAK_EVENT)
                            except Exception:
                                pass
                        else:
                            self._process.terminate()
                        self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1)
            except Exception as exc:
                print(f"[ScreenRecorder] Error stopping ffmpeg process: {exc}")
            finally:
                self._process = None

        if self._fallback_thread is not None:
            self._fallback_thread.join(timeout=5)
            self._fallback_thread = None

        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
            self._writer = None

        if self._mss is not None:
            try:
                self._mss.close()
            except Exception:
                pass
            self._mss = None
        self._monitor = None

    def get_first_frame_timestamp_s(self) -> Optional[float]:
        """Return the monotonic offset (in seconds) from t0 when recording started."""
        return self._first_frame_timestamp_s

    def _start_ffmpeg_recording(self) -> None:
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        command = [
            self.ffmpeg_path,
            "-y",
            "-f",
            "gdigrab" if os.name == "nt" else "x11grab",
            "-framerate",
            str(self.fps),
            "-draw_mouse",
            "1",
            "-i",
            "desktop" if os.name == "nt" else ":0.0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(self.output_path),
        ]

        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )

    def _start_mss_fallback(self) -> None:
        width, height = 1920, 1080
        try:
            self._mss = mss.MSS()
            self._monitor = self._mss.monitors[1]
            width = self._monitor["width"]
            height = self._monitor["height"]
        except Exception as exc:
            print(f"[ScreenRecorder] mss monitor detection warning: {exc}. Using default 1920x1080 resolution.")

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(
            str(self.output_path),
            fourcc,
            float(self.fps),
            (width, height),
        )
        if not self._writer.isOpened():
            raise RuntimeError("Unable to open OpenCV VideoWriter for screen capture fallback.")

        self._fallback_thread = threading.Thread(target=self._fallback_loop, daemon=True)
        self._fallback_thread.start()

    def _fallback_loop(self) -> None:
        if self._writer is not None and not self._writer.isOpened():
            return
        frame_interval = 1.0 / self.fps
        width, height = 1920, 1080
        if self._monitor:
            width = self._monitor.get("width", 1920)
            height = self._monitor.get("height", 1080)

        while not self._stop_event.is_set():
            t_start = time.monotonic()
            frame_bgr = None
            if self._mss is not None and self._monitor is not None:
                try:
                    frame = self._mss.grab(self._monitor)
                    frame_array = np.asarray(frame)
                    frame_bgr = cv2.cvtColor(frame_array, cv2.COLOR_BGRA2BGR)
                    frame_bgr = self._draw_cursor_overlay(frame_bgr)
                except Exception:
                    # In headless/lock-screen environment, graphics capture GDI BitBlt fails
                    frame_bgr = None

            if frame_bgr is None:
                # Generate synthetic fallback frame indicating screen recording status
                frame_bgr = np.zeros((height, width, 3), dtype=np.uint8)
                cv2.putText(
                    frame_bgr,
                    f"Recording Screen (Headless/Restricted) | t={time.monotonic() - (self.t0 or 0):.2f}s",
                    (50, height // 2),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (0, 255, 0),
                    2,
                )

            if self._writer is not None:
                self._writer.write(frame_bgr)

            elapsed = time.monotonic() - t_start
            sleep_time = max(0.0, frame_interval - elapsed)
            if self._stop_event.wait(timeout=sleep_time):
                break
