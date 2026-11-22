from __future__ import annotations

import os
import logging
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
from pynput.mouse import Controller as MouseController
from ffmpeg_utils import find_ffmpeg

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
    Fails clearly if neither backend can capture real desktop frames.
    """

    def __init__(self, output_path: str, fps: int = 15) -> None:
        if not isinstance(fps, int) or not 1 <= fps <= 120:
            raise ValueError("FPS must be an integer between 1 and 120.")
        self.output_path = Path(output_path)
        self.fps = fps
        self.logger = logging.getLogger(__name__)
        self.t0: Optional[float] = None
        self.ffmpeg_path: Optional[str] = self._find_ffmpeg()
        self._process: Optional[subprocess.Popen[str]] = None
        self._ffmpeg_progress_thread: Optional[threading.Thread] = None
        self._mss = None
        self._writer = None
        self._monitor = None
        self._fallback_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._first_frame_event = threading.Event()
        self._first_frame_timestamp_s: Optional[float] = None
        self._capture_error: Optional[Exception] = None
        self._using_ffmpeg = False
        self._mouse_controller = MouseController()

    @property
    def backend(self) -> str:
        """Name of the active screen-capture backend."""
        return "ffmpeg" if self._using_ffmpeg else "opencv"

    def _find_ffmpeg(self) -> Optional[str]:
        """Locate the FFmpeg executable in the install-local ffmpeg folder."""
        return find_ffmpeg()

    def start(self, t0: float) -> None:
        """Start screen recording aligned with session monotonic clock t0."""
        if self._process is not None or self._fallback_thread is not None:
            raise RuntimeError("Screen recording is already active.")
        self.t0 = t0
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._first_frame_event.clear()
        self._first_frame_timestamp_s = None
        self._capture_error = None
        self._using_ffmpeg = False

        if self.ffmpeg_path:
            try:
                self._start_ffmpeg_recording()
                if self._first_frame_event.wait(timeout=10.0) and self._first_frame_timestamp_s is not None:
                    if self._process is not None and self._process.poll() is None:
                        self._using_ffmpeg = True
                        self.logger.info(
                            "FFmpeg screen capture started (first frame %.6fs after t0).",
                            self._first_frame_timestamp_s,
                        )
                        return
                exit_code = self._process.poll() if self._process is not None else None
                self.logger.warning(
                    "FFmpeg did not produce a frame (exit code %s); trying the OpenCV fallback.",
                    exit_code,
                )
                self._close_ffmpeg_process()
            except Exception as exc:
                self.logger.warning("FFmpeg startup failed; trying the OpenCV fallback: %s", exc)
                self._close_ffmpeg_process()

        if mss is not None and cv2 is not None:
            try:
                self._start_mss_fallback()
                if not self._first_frame_event.wait(timeout=10.0):
                    raise RuntimeError("The screen capture fallback did not produce a frame within 10 seconds.")
                if self._capture_error is not None:
                    raise RuntimeError(f"The screen capture fallback failed: {self._capture_error}") from self._capture_error
                if self._first_frame_timestamp_s is None:
                    raise RuntimeError("The screen capture fallback stopped before producing its first frame.")
                self.logger.info(
                    "OpenCV screen capture started (first frame %.6fs after t0).",
                    self._first_frame_timestamp_s,
                )
                return
            except Exception:
                try:
                    self.stop()
                except Exception:
                    self.logger.exception("Error while cleaning up a failed screen-capture startup.")
                raise

        raise RuntimeError(
            "Screen recording could not start. Install FFmpeg, or install mss and opencv-python "
            "and make sure desktop capture is available."
        )

    def stop(self) -> None:
        """Gracefully stop recording and finalize video container header (MOOV atom)."""
        self._stop_event.set()
        errors: list[str] = []

        if self._process is not None:
            process = self._process
            try:
                self._close_ffmpeg_process()
                if process.returncode not in (None, 0):
                    errors.append(f"FFmpeg exited with code {process.returncode}.")
            except Exception as exc:
                errors.append(f"Could not stop FFmpeg cleanly: {exc}")

        if self._fallback_thread is not None:
            self._fallback_thread.join(timeout=5)
            if self._fallback_thread.is_alive():
                errors.append("The screen-capture thread did not stop within 5 seconds.")
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

        if self._capture_error is not None:
            errors.append(f"Screen capture failed: {self._capture_error}")
        self._using_ffmpeg = False
        if errors:
            raise RuntimeError(" ".join(errors))

    def get_first_frame_timestamp_s(self) -> Optional[float]:
        """Return the monotonic offset (in seconds) from t0 when recording started."""
        return self._first_frame_timestamp_s

    def _start_ffmpeg_recording(self) -> None:
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            if os.name == "nt" else 0
        )
        command = [
            self.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
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
            "-progress",
            "pipe:1",
            "-stats_period",
            "0.05",
            "-nostats",
            str(self.output_path),
        ]

        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            creationflags=creation_flags,
        )
        self._ffmpeg_progress_thread = threading.Thread(
            target=self._read_ffmpeg_progress,
            args=(self._process,),
            daemon=True,
            name="ScreenRecorder-FFmpegProgress",
        )
        self._ffmpeg_progress_thread.start()

    def _read_ffmpeg_progress(self, process: subprocess.Popen[str]) -> None:
        """Record t0 offset when FFmpeg reports that it encoded its first frame."""
        try:
            if process.stdout is None:
                return
            for line in process.stdout:
                if self._first_frame_timestamp_s is not None or not line.startswith("frame="):
                    continue
                try:
                    frame_count = int(line.partition("=")[2].strip())
                except ValueError:
                    continue
                if frame_count > 0:
                    self._first_frame_timestamp_s = time.monotonic() - (self.t0 or 0.0)
                    self._first_frame_event.set()
        except Exception as exc:
            self.logger.warning("Could not read FFmpeg progress: %s", exc)
        finally:
            if self._first_frame_timestamp_s is None:
                self._first_frame_event.set()

    def _close_ffmpeg_process(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                try:
                    process.stdin.write("q\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    try:
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    except Exception:
                        process.terminate()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if self._ffmpeg_progress_thread is not None:
                self._ffmpeg_progress_thread.join(timeout=2)
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except OSError:
                    pass
            if self._ffmpeg_progress_thread is not None:
                if self._ffmpeg_progress_thread.is_alive():
                    self._ffmpeg_progress_thread.join(timeout=1)
                self._ffmpeg_progress_thread = None
            self._process = None

    def _start_mss_fallback(self) -> None:
        try:
            self._mss = mss.MSS()
            if len(self._mss.monitors) < 2:
                raise RuntimeError("No desktop monitor is available for capture.")
            self._monitor = self._mss.monitors[1]
        except Exception as exc:
            if self._mss is not None:
                self._mss.close()
                self._mss = None
            raise RuntimeError(f"Could not initialize desktop capture: {exc}") from exc

        width = int(self._monitor["width"])
        height = int(self._monitor["height"])
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(
            str(self.output_path),
            fourcc,
            float(self.fps),
            (width, height),
        )
        if not self._writer.isOpened():
            self._writer.release()
            self._writer = None
            self._mss.close()
            self._mss = None
            raise RuntimeError("Unable to open OpenCV VideoWriter for screen capture fallback.")

        self._fallback_thread = threading.Thread(target=self._fallback_loop, daemon=True)
        self._fallback_thread.start()

    def _fallback_loop(self) -> None:
        frame_interval = 1.0 / self.fps
        try:
            while not self._stop_event.is_set():
                t_start = time.monotonic()
                if self._mss is None or self._monitor is None or self._writer is None:
                    raise RuntimeError("Desktop capture resources are not initialized.")
                frame = self._mss.grab(self._monitor)
                frame_bgr = cv2.cvtColor(np.asarray(frame), cv2.COLOR_BGRA2BGR)
                frame_bgr = self._draw_cursor_overlay(frame_bgr)
                self._writer.write(frame_bgr)
                if self._first_frame_timestamp_s is None:
                    self._first_frame_timestamp_s = time.monotonic() - (self.t0 or 0.0)
                    self._first_frame_event.set()

                elapsed = time.monotonic() - t_start
                if self._stop_event.wait(timeout=max(0.0, frame_interval - elapsed)):
                    break
        except Exception as exc:
            self._capture_error = exc
            self._first_frame_event.set()
            self._stop_event.set()
            self.logger.exception("OpenCV desktop capture stopped after an error.")

    def _draw_cursor_overlay(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Draw a small visible marker at the current cursor position."""
        try:
            if self._monitor is None:
                return frame_bgr
            mouse_x, mouse_y = self._mouse_controller.position
            x = int(mouse_x - self._monitor.get("left", 0))
            y = int(mouse_y - self._monitor.get("top", 0))
            height, width = frame_bgr.shape[:2]
            if 0 <= x < width and 0 <= y < height:
                cv2.circle(frame_bgr, (x, y), 9, (0, 0, 0), 2)
                cv2.circle(frame_bgr, (x, y), 5, (255, 255, 255), -1)
        except Exception:
            self.logger.debug("Could not draw the cursor marker.", exc_info=True)
        return frame_bgr
