"""
settings_server.py — Local Flask web server for Usability Recorder settings panel.

Runs on http://127.0.0.1:5000 in a background daemon thread.
Provides REST API consumed by web_panel/index.html.
"""
from __future__ import annotations

import base64
import csv
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, request, send_from_directory

from app_paths import (
    get_app_data_dir,
    get_config_path,
    get_default_sessions_dir,
    get_legacy_app_data_dir,
    is_frozen,
    resolve_user_path,
)
from ffmpeg_utils import require_ffmpeg


# ─── Video Processor (pure Python — no Qt) ───────────────────────────────────

class VideoProcessorRunner:
    """Runs click-overlay video processing in a background daemon thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.is_running  = False
        self.progress    = 0
        self.status_msg  = "Idle"
        self.result: Optional[Dict[str, Any]] = None
        self.session_id: Optional[str] = None
        self._cancel     = False
        self._thread: Optional[threading.Thread] = None
        self._latest_jpeg: Optional[bytes] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, session_dir: Path) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.is_running  = True
            self.progress    = 0
            self.status_msg  = "Starting…"
            self.result      = None
            self._cancel     = False
            self._latest_jpeg = None
            self.session_id  = session_dir.name
        self._thread = threading.Thread(
            target=self._run, args=(session_dir,), daemon=True, name="VideoProc"
        )
        self._thread.start()
        return True

    def cancel(self) -> None:
        with self._lock:
            self._cancel = True

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "is_running":  self.is_running,
                "progress":    self.progress,
                "status":      self.status_msg,
                "result":      self.result,
                "session_id":  self.session_id,
            }

    def get_preview_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    # ── Internal ──────────────────────────────────────────────────────────────

    def _upd(self, progress: Optional[int] = None, status: Optional[str] = None) -> None:
        with self._lock:
            if progress is not None:
                self.progress = progress
            if status is not None:
                self.status_msg = status

    def _done(self, success: bool, msg: str) -> None:
        with self._lock:
            self.is_running = False
            self.result     = {"success": success, "message": msg}
            self.status_msg = msg
            if success:
                self.progress = 100

    def _run(self, session_dir: Path) -> None:
        try:
            self._process(session_dir)
        except Exception as exc:
            self._done(False, str(exc))

    def _process(self, session_dir: Path) -> None:
        try:
            import cv2 as _cv2
        except ImportError:
            self._done(False, "OpenCV not available — run: pip install opencv-python")
            return

        events_csv     = session_dir / "events.csv"
        original_video = session_dir / "screen_recording.mp4"
        temp_video     = session_dir / "temp_video_no_audio.mp4"
        final_video    = session_dir / "screen_recording_with_clicks.mp4"

        if not events_csv.exists() or not original_video.exists():
            self._done(False, "Missing events.csv or screen_recording.mp4")
            return

        self._upd(status="Parsing click events…")

        # Read the measured first-frame offset so clicks align with the captured video.
        video_offset = 0.0
        meta_file = session_dir / "session_metadata.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                video_offset = float(meta.get("screen_first_frame_timestamp_s") or 0.0)
                # Older sessions did not record the capture backend; retain their
                # historical correction while trusting measured offsets for new files.
                if "screen_capture_backend" not in meta and 0.0 < video_offset < 0.1:
                    video_offset = 0.35
            except Exception:
                pass
        self._upd(status=f"Video offset: {video_offset:.3f}s. Parsing clicks…")

        clicks = self._parse_clicks(events_csv, video_offset)
        self._upd(status=f"Found {len(clicks)} clicks (offset {video_offset:.3f}s). Processing video…")

        cap = _cv2.VideoCapture(str(original_video))
        if not cap.isOpened():
            self._done(False, "Cannot open video file")
            return

        fps          = cap.get(_cv2.CAP_PROP_FPS) or 25.0
        width        = int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH))
        height       = int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))

        fourcc = _cv2.VideoWriter_fourcc(*"mp4v")
        out    = _cv2.VideoWriter(str(temp_video), fourcc, fps, (width, height))

        frame_idx = 0
        while cap.isOpened():
            with self._lock:
                if self._cancel:
                    break
            ret, frame = cap.read()
            if not ret:
                break

            msec = cap.get(_cv2.CAP_PROP_POS_MSEC)
            # Use millisecond timestamp from the video stream to support variable frame rate (VFR)
            if msec >= 0:
                t = msec / 1000.0
            else:
                t = frame_idx / fps

            active = [c for c in clicks if c["start"] <= t <= c["end"]]

            for c in active:
                life  = (t - c["start"]) / max(c["end"] - c["start"], 0.001)
                alpha = max(0.0, 1.0 - life)
                btn   = c["button"].lower()
                if "left" in btn:
                    col, lbl = (0, 0, int(255*alpha)), "L"
                elif "right" in btn:
                    col, lbl = (int(40*alpha), int(235*alpha), int(245*alpha)), "R"
                else:
                    col, lbl = (0, int(255*alpha), 0), "M"
                x, y = int(c["x"]), int(c["y"])
                r    = 10 + int(life * 20)
                _cv2.circle(frame, (x, y), r, col, 3)
                _cv2.putText(frame, lbl, (x+r+4, y+5), _cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)

            out.write(frame)

            # Store JPEG preview every 6 frames
            if frame_idx % 6 == 0:
                pw = min(width, 720)
                ph = int(pw * height / width)
                prev = _cv2.resize(frame, (pw, ph))
                ok, buf = _cv2.imencode(".jpg", prev, [_cv2.IMWRITE_JPEG_QUALITY, 55])
                if ok:
                    with self._lock:
                        self._latest_jpeg = bytes(buf)

            frame_idx += 1
            if total_frames > 0 and frame_idx % 15 == 0:
                pct = int(frame_idx / total_frames * 88)
                self._upd(progress=pct,
                          status=f"Frame {frame_idx}/{total_frames} ({pct}%)")

        cap.release()
        out.release()

        with self._lock:
            cancelled = self._cancel
        if cancelled:
            if temp_video.exists():
                temp_video.unlink()
            self._done(False, "Cancelled by user")
            return

        self._upd(progress=92, status="Muxing audio with FFmpeg…")
        try:
            ffmpeg = require_ffmpeg()
            command = [
                ffmpeg, "-y",
                "-i", str(temp_video),
                "-i", str(original_video),
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "copy",
                "-map", "0:v:0", "-map", "1:a:0?",
                str(final_video),
            ]
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            if temp_video.exists():
                temp_video.unlink()
            self._done(True, f"Done! Saved as: {final_video.name}")
        except Exception as exc:
            self._done(False, f"FFmpeg failed: {exc}")

    @staticmethod
    def _parse_clicks(events_csv: Path, video_offset: float = 0.0) -> List[Dict[str, Any]]:
        """Parse mouse_click events and shift timestamps by video_offset so they
        align with video-frame time (frame_idx / fps) rather than t0-relative time."""
        clicks: List[Dict[str, Any]] = []
        try:
            with events_csv.open("r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("event_type") != "mouse_click":
                        continue
                    try:
                        detail = json.loads(row["detail"])
                        if detail.get("pressed"):
                            # Subtract offset so click appears on the correct video frame
                            ts = float(row["timestamp_s"]) - video_offset
                            if ts < 0:
                                ts = 0.0  # clamp negative values (very early clicks)
                            clicks.append({
                                "start":  ts,
                                "end":    ts + 0.8,
                                "x":      detail.get("x", 0),
                                "y":      detail.get("y", 0),
                                "button": detail.get("button", ""),
                            })
                    except Exception:
                        pass
        except Exception:
            pass
        return clicks


# Module-level runner singleton
video_runner = VideoProcessorRunner()


# ─── Shared Application State ────────────────────────────────────────────────

def _same_path(first: str | Path, second: str | Path) -> bool:
    try:
        return os.path.normcase(str(Path(first).expanduser().resolve())) == os.path.normcase(
            str(Path(second).expanduser().resolve())
        )
    except (OSError, TypeError, ValueError):
        return False


def _move_legacy_sessions(old_sessions: Path, new_sessions: Path) -> bool:
    """Move the previous default session folder next to the new installation."""
    if not old_sessions.is_dir() or _same_path(old_sessions, new_sessions):
        return True

    try:
        items = list(old_sessions.iterdir())
        new_sessions.mkdir(parents=True, exist_ok=True)
        conflicts = [item.name for item in items if (new_sessions / item.name).exists()]
        if conflicts:
            logging.getLogger(__name__).warning(
                "Could not move old sessions because the destination already contains: %s",
                ", ".join(conflicts),
            )
            return False
        for item in items:
            destination = new_sessions / item.name
            shutil.move(str(item), str(destination))
        old_sessions.rmdir()
        return True
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Could not move old sessions from %s to %s: %s",
            old_sessions,
            new_sessions,
            exc,
        )
        return False

class AppState:
    """Thread-safe shared state between Flask server and the Qt GUI thread."""

    _CONFIG_DEFAULTS: Dict[str, Any] = {
        "participant_id":  "",
        "task_label":      "",
        "session_label":   "",
        "session_root":    "sessions",
        "fps":             15,
        "hotkey":          "Ctrl+Alt+F9",
        "privacy_mode":    False,
        "tracking_mode":   "disabled",
        "web_tracker_port": 9877,
        "pupil_mode":      "disabled",
        "pupil_ip":        "127.0.0.1",
        "pupil_port":      50020,
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Mutable config (kept in sync with config.json)
        self._config: Dict[str, Any] = dict(self._CONFIG_DEFAULTS)
        self._config["session_root"] = str(get_default_sessions_dir())

        # Recording runtime state
        self._recording = False
        self._recording_start: Optional[float] = None
        self._current_session_dir: Optional[str] = None
        self._last_summary: Optional[Dict[str, Any]] = None

        # Load persisted config
        self._load_config_file()

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config_file(self) -> None:
        p = get_config_path()
        if not p.exists():
            if is_frozen():
                old_sessions = get_legacy_app_data_dir() / "sessions"
                new_sessions = get_default_sessions_dir()
                if old_sessions.exists() and not _move_legacy_sessions(old_sessions, new_sessions):
                    with self._lock:
                        self._config["session_root"] = str(old_sessions)
                    self.save_config_file()
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            save_migrated_config = False
            old_sessions = get_legacy_app_data_dir() / "sessions"
            session_root = data.get("session_root")
            uses_legacy_default = (
                not session_root
                or _same_path(session_root, old_sessions)
            )
            if is_frozen() and uses_legacy_default:
                new_sessions = get_default_sessions_dir()
                session_root = (
                    new_sessions
                    if _move_legacy_sessions(old_sessions, new_sessions)
                    else old_sessions
                )
                save_migrated_config = True
            with self._lock:
                for k in self._CONFIG_DEFAULTS:
                    if k in data:
                        self._config[k] = data[k]
                self._config["session_root"] = str(resolve_user_path(session_root))
            if save_migrated_config:
                self.save_config_file()
        except Exception:
            pass

    def save_config_file(self) -> None:
        with self._lock:
            payload = dict(self._config)
        config_path = get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = config_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_path.replace(config_path)

    @property
    def config(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def update_config(self, updates: Dict[str, Any]) -> None:
        allowed = set(self._CONFIG_DEFAULTS.keys())
        updates = dict(updates)
        if "session_root" in updates:
            updates["session_root"] = str(resolve_user_path(updates["session_root"]))
        with self._lock:
            for k, v in updates.items():
                if k in allowed:
                    self._config[k] = v
        self.save_config_file()

    # ── Recording State ───────────────────────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def set_recording(
        self,
        recording: bool,
        session_dir: Optional[str] = None,
        started_monotonic: Optional[float] = None,
    ) -> None:
        with self._lock:
            self._recording = recording
            if recording:
                self._recording_start = (
                    started_monotonic if started_monotonic is not None else time.monotonic()
                )
                self._current_session_dir = session_dir
            else:
                self._recording_start = None

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            elapsed = 0.0
            if self._recording and self._recording_start is not None:
                elapsed = time.monotonic() - self._recording_start
            return {
                "is_recording":    self._recording,
                "elapsed_seconds": int(elapsed),
                "session_dir":     self._current_session_dir,
                "participant_id":  self._config.get("participant_id", ""),
                "task_label":      self._config.get("task_label", ""),
            }

    def set_last_summary(self, summary: Dict[str, Any]) -> None:
        with self._lock:
            self._last_summary = summary

    def get_last_summary(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._last_summary

# ─── Flask Server ─────────────────────────────────────────────────────────────

class SettingsServer:
    """Runs a Flask web server on localhost in a background daemon thread."""

    WEB_PANEL_DIR = Path(__file__).parent / "web_panel"

    def __init__(self, state: AppState, port: int = 5000) -> None:
        self.state = state
        self.port = port
        self._thread: Optional[threading.Thread] = None

        # Silence Flask / Werkzeug console output
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        self.app = Flask(__name__, static_folder=None)
        self.app.logger.setLevel(logging.ERROR)
        self._register_routes()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="SettingsServer",
        )
        self._thread.start()

    def _run(self) -> None:
        self.app.run(
            host="127.0.0.1",
            port=self.port,
            debug=False,
            use_reloader=False,
        )

    # ── Route Registration ─────────────────────────────────────────────────────

    def _register_routes(self) -> None:
        app  = self.app
        state = self.state
        panel_dir = str(self.WEB_PANEL_DIR)

        # ── Static / index ───────────────────────────────────────────────────

        @app.route("/")
        def index():
            return send_from_directory(panel_dir, "index.html")

        # ── Status ───────────────────────────────────────────────────────────

        @app.route("/api/status")
        def api_status():
            return jsonify(state.get_status())

        # ── Config ───────────────────────────────────────────────────────────

        @app.route("/api/config", methods=["GET"])
        def api_config_get():
            return jsonify(state.config)

        @app.route("/api/config", methods=["POST"])
        def api_config_post():
            data = request.get_json(force=True, silent=True) or {}
            state.update_config(data)
            return jsonify({"ok": True})

        # ── Sessions List ─────────────────────────────────────────────────────

        @app.route("/api/sessions")
        def api_sessions():
            root = Path(state.config.get("session_root", get_default_sessions_dir()))
            sessions = []
            if root.exists():
                for d in sorted(root.iterdir(), reverse=True):
                    if not d.is_dir():
                        continue
                    meta_file = d / "session_metadata.json"
                    if not meta_file.exists():
                        continue
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        analysis: Dict[str, Any] = {}
                        af = d / "analysis.json"
                        if af.exists():
                            try:
                                analysis = json.loads(af.read_text(encoding="utf-8"))
                            except Exception:
                                pass
                        # Check which files exist
                        has_video = (d / "screen_recording.mp4").exists()
                        has_web   = (d / "web_page_visits.csv").exists()
                        has_events = (d / "events.csv").exists()
                        sessions.append({
                            "id":        d.name,
                            "path":      str(d),
                            "metadata":  meta,
                            "analysis":  analysis,
                            "has_video": has_video,
                            "has_web":   has_web,
                            "has_events": has_events,
                        })
                    except Exception:
                        continue
            return jsonify(sessions)

        # ── Open Session Folder ───────────────────────────────────────────────

        @app.route("/api/sessions/<session_id>/open_folder", methods=["POST"])
        def api_open_folder(session_id: str):
            root = Path(state.config.get("session_root", get_default_sessions_dir()))
            d = root / session_id
            if not d.exists():
                return jsonify({"ok": False, "error": "Not found"}), 404
            try:
                os.startfile(str(d))
            except Exception:
                subprocess.Popen(["explorer", str(d)])
            return jsonify({"ok": True})

        # ── Analyze ───────────────────────────────────────────────────────────

        @app.route("/api/analyze", methods=["POST"])
        def api_analyze():
            try:
                from analyzer import batch_analyze_sessions
                root   = state.config.get("session_root", str(get_default_sessions_dir()))
                output = batch_analyze_sessions(root, get_app_data_dir() / "sessions_summary.csv")
                return jsonify({"ok": True, "output": str(output)})
            except Exception as exc:
                return jsonify({"ok": False, "error": str(exc)}), 500

        # ── Export CSV ────────────────────────────────────────────────────────

        @app.route("/api/export", methods=["POST"])
        def api_export():
            try:
                from analyzer import batch_analyze_sessions
                root   = state.config.get("session_root", str(get_default_sessions_dir()))
                output = batch_analyze_sessions(root, get_app_data_dir() / "sessions_summary.csv")
                return jsonify({"ok": True, "output": str(output)})
            except Exception as exc:
                return jsonify({"ok": False, "error": str(exc)}), 500

        # ── Video Processor ───────────────────────────────────────────────────

        @app.route("/api/video/sessions")
        def api_video_sessions():
            """Return sessions that have both events.csv and screen_recording.mp4."""
            root = Path(state.config.get("session_root", get_default_sessions_dir()))
            result = []
            if root.exists():
                for d in sorted(root.iterdir(), reverse=True):
                    if not d.is_dir():
                        continue
                    has_e = (d / "events.csv").exists()
                    has_v = (d / "screen_recording.mp4").exists()
                    has_o = (d / "screen_recording_with_clicks.mp4").exists()
                    if has_e and has_v:
                        result.append({
                            "id":         d.name,
                            "has_output": has_o,
                        })
            return jsonify(result)

        @app.route("/api/video/start", methods=["POST"])
        def api_video_start():
            data       = request.get_json(force=True, silent=True) or {}
            session_id = data.get("session_id", "")
            root       = Path(state.config.get("session_root", get_default_sessions_dir()))
            sess_dir   = root / session_id
            if not sess_dir.is_dir():
                return jsonify({"ok": False, "error": "Session not found"}), 404
            started = video_runner.start(sess_dir)
            if not started:
                return jsonify({"ok": False, "error": "Already running"}), 409
            return jsonify({"ok": True})

        @app.route("/api/video/status")
        def api_video_status():
            return jsonify(video_runner.get_status())

        @app.route("/api/video/cancel", methods=["POST"])
        def api_video_cancel():
            video_runner.cancel()
            return jsonify({"ok": True})

        @app.route("/api/video/preview.jpg")
        def api_video_preview():
            jpeg = video_runner.get_preview_jpeg()
            if not jpeg:
                # Return a tiny 1×1 transparent placeholder
                return Response(status=204)
            return Response(jpeg, mimetype="image/jpeg",
                            headers={"Cache-Control": "no-store"})

        # ── Last Session Summary ──────────────────────────────────────────────

        @app.route("/api/last_summary")
        def api_last_summary():
            s = state.get_last_summary()
            return jsonify(s or {})


# ─── Module-level singleton ───────────────────────────────────────────────────

app_state      = AppState()
settings_server = SettingsServer(app_state, port=5000)
