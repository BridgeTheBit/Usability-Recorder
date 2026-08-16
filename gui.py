"""
gui.py — Minimal desktop recording widget for Usability Testing Recorder.

Only two controls:
  • Start / Stop button  (large, changes color/label while recording)
  • ⚙ Gear button        (opens http://127.0.0.1:5000 – the settings web panel)

All settings, session management, export, and post-processing live in the
web panel served by settings_server.py.
"""
from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from settings_server import AppState, SettingsServer, app_state, settings_server
from web_tracker import WebTracker

try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None

try:
    from video_processor import VideoProcessorWindow
except Exception:
    VideoProcessorWindow = None


def _parse_hotkey(hotkey_str: str) -> str:
    parts = [p.strip().lower() for p in hotkey_str.split("+") if p.strip()]
    out = []
    for p in parts:
        if p in ("ctrl", "control"):
            out.append("<ctrl>")
        elif p == "alt":
            out.append("<alt>")
        elif p == "shift":
            out.append("<shift>")
        elif p in ("cmd", "win", "super"):
            out.append("<cmd>")
        elif p.startswith("f") and p[1:].isdigit():
            out.append(f"<{p}>")
        else:
            out.append(p)
    return "+".join(out)


# ─── Minimal Recording Widget ─────────────────────────────────────────────────

class RecorderWidget(QWidget):
    stop_requested = Signal()

    # ── Styles ────────────────────────────────────────────────────────────────

    _WINDOW_STYLE = """
        RecorderWidget {
            background-color: #0d1117;
            border-radius: 12px;
        }
    """
    _BTN_IDLE = """
        QPushButton {
            background-color: #238636;
            color: #ffffff;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 0.3px;
        }
        QPushButton:hover   { background-color: #2ea043; }
        QPushButton:pressed { background-color: #196127; }
        QPushButton:disabled{ background-color: #1c2128; color: #484f58; }
    """
    _BTN_REC = """
        QPushButton {
            background-color: #da3633;
            color: #ffffff;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 0.3px;
        }
        QPushButton:hover   { background-color: #f85149; }
        QPushButton:pressed { background-color: #b02a27; }
    """
    _GEAR_STYLE = """
        QPushButton {
            background-color: transparent;
            border: 1px solid #30363d;
            border-radius: 8px;
            color: #8b949e;
            font-size: 18px;
        }
        QPushButton:hover   { background-color: #21262d; color: #58a6ff; border-color: #58a6ff; }
        QPushButton:pressed { background-color: #161b22; }
    """

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self._state: AppState = app_state
        self._server: SettingsServer = settings_server

        self._web_tracker: Optional[WebTracker] = None
        self._video_proc_win = None
        self._hotkey_listener = None
        self._elapsed_timer: Optional[QTimer] = None
        self._elapsed_seconds = 0

        self.stop_requested.connect(self.on_stop)

        self._build_ui()
        self._apply_saved_tracking()
        self._register_hotkey(self._state.config.get("hotkey", "Ctrl+Alt+F9"))

        # Poll for web-panel-initiated actions every 500 ms
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(500)

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setWindowTitle("Usability Recorder")
        self.setFixedSize(360, 130)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(self._WINDOW_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ── Header row ────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #484f58; font-size: 11px;")

        self._title = QLabel("Usability Test Recorder")
        self._title.setStyleSheet("color: #8b949e; font-size: 12px; font-weight: 600;")

        hdr.addWidget(self._dot)
        hdr.addWidget(self._title)
        hdr.addStretch()

        self._gear_btn = QPushButton("⚙")
        self._gear_btn.setFixedSize(32, 32)
        self._gear_btn.setToolTip("Open Settings & Sessions Panel (localhost:5000)")
        self._gear_btn.setStyleSheet(self._GEAR_STYLE)
        self._gear_btn.clicked.connect(self._open_panel)
        hdr.addWidget(self._gear_btn)

        root.addLayout(hdr)

        # ── Status ────────────────────────────────────────────────────────────
        self._status = QLabel("Ready — open ⚙ to configure")
        self._status.setStyleSheet("color: #484f58; font-size: 11px;")
        root.addWidget(self._status)

        # ── Record button ─────────────────────────────────────────────────────
        self._rec_btn = QPushButton("▶   Start Recording")
        self._rec_btn.setFixedHeight(44)
        self._rec_btn.setStyleSheet(self._BTN_IDLE)
        self._rec_btn.clicked.connect(self._toggle_recording)
        root.addWidget(self._rec_btn)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _toggle_recording(self) -> None:
        if self._state.is_recording:
            self.on_stop()
        else:
            self.on_start()

    def _open_panel(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self._server.port}")

    def on_start(self) -> None:
        cfg = self._state.config
        participant_id = cfg.get("participant_id", "").strip()

        if not participant_id:
            self._open_panel()
            QMessageBox.warning(
                self,
                "Participant ID Required",
                "Please fill in the Participant ID in the Settings Panel\n"
                "(⚙ button) before starting the recording.",
            )
            return

        try:
            tracking_mode    = cfg.get("tracking_mode", "disabled")
            web_tracker_port = int(cfg.get("web_tracker_port", 9877))

            if tracking_mode == "web":
                self._ensure_web_tracker(web_tracker_port)

            self.controller.session_root = Path(cfg.get("session_root", "sessions"))

            metadata = self.controller.start_session(
                participant_id  = participant_id,
                task_label      = cfg.get("task_label", ""),
                session         = cfg.get("session_label", ""),
                fps             = int(cfg.get("fps", 15)),
                full_key_logging= not cfg.get("privacy_mode", False),
                pupil_mode      = cfg.get("pupil_mode", "zmq"),
                pupil_ip        = cfg.get("pupil_ip", "127.0.0.1"),
                pupil_port      = int(cfg.get("pupil_port", 50020)),
                tracking_mode   = tracking_mode,
                web_tracker_port= web_tracker_port,
                existing_web_tracker = self._web_tracker if tracking_mode == "web" else None,
            )

            session_dir = str(getattr(self.controller, "session_dir", ""))
            self._state.set_recording(True, session_dir)
            self._start_elapsed_timer()

            self._rec_btn.setStyleSheet(self._BTN_REC)
            self._rec_btn.setText("⏹   Stop Recording")
            self._dot.setStyleSheet("color: #f85149; font-size: 11px;")
            self._title.setStyleSheet("color: #f85149; font-size: 12px; font-weight: 600;")
            self._update_status_label()

            self.showMinimized()

        except Exception as exc:
            QMessageBox.critical(self, "Session Startup Error", str(exc))

    def on_stop(self) -> None:
        if not self._state.is_recording:
            return
        try:
            summary = self.controller.stop_session()
            self._state.set_recording(False)
            self._state.set_last_summary(summary)
            self._stop_elapsed_timer()

            self._rec_btn.setStyleSheet(self._BTN_IDLE)
            self._rec_btn.setText("▶   Start Recording")
            self._dot.setStyleSheet("color: #484f58; font-size: 11px;")
            self._title.setStyleSheet("color: #8b949e; font-size: 12px; font-weight: 600;")

            metrics = summary.get("analysis_metrics", {})
            dur = metrics.get("task_duration_formatted", "—")
            clicks = metrics.get("total_clicks", 0)
            self._status.setText(f"Last session: {dur} | {clicks} clicks")
            self._status.setStyleSheet("color: #58a6ff; font-size: 11px;")

            self.showNormal()
            self.activateWindow()

        except Exception as exc:
            QMessageBox.critical(self, "Session Stop Error", str(exc))

    # ── Elapsed Timer ─────────────────────────────────────────────────────────

    def _start_elapsed_timer(self) -> None:
        self._elapsed_seconds = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._elapsed_timer.start(1000)

    def _stop_elapsed_timer(self) -> None:
        if self._elapsed_timer:
            self._elapsed_timer.stop()
            self._elapsed_timer = None

    def _tick_elapsed(self) -> None:
        self._elapsed_seconds += 1
        self._update_status_label()

    def _update_status_label(self) -> None:
        cfg = self._state.config
        pid = cfg.get("participant_id", "—")
        s   = self._elapsed_seconds
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        elapsed = f"{h:02d}:{m:02d}:{sec:02d}"
        self._status.setText(f"● REC  {elapsed}   |   {pid}")
        self._status.setStyleSheet("color: #f85149; font-size: 11px; font-weight: 600;")

    # ── Poll (actions from web panel) ─────────────────────────────────────────

    def _poll(self) -> None:
        actions = self._state.poll_pending_actions()
        if actions.get("open_video_processor"):
            self._open_video_processor()

    # ── Web Tracker ───────────────────────────────────────────────────────────

    def _ensure_web_tracker(self, port: int) -> None:
        if self._web_tracker and self._web_tracker.port == port:
            return
        if self._web_tracker:
            try:
                self._web_tracker.stop_server()
            except Exception:
                pass
        self._web_tracker = WebTracker(port=port)
        self._web_tracker.start_server()

    def _apply_saved_tracking(self) -> None:
        cfg = self._state.config
        if cfg.get("tracking_mode") == "web":
            self._ensure_web_tracker(int(cfg.get("web_tracker_port", 9877)))

    # ── Video Processor ───────────────────────────────────────────────────────

    def _open_video_processor(self) -> None:
        if VideoProcessorWindow is None:
            return
        if self._video_proc_win is None:
            self._video_proc_win = VideoProcessorWindow()
        self._video_proc_win.show()
        self._video_proc_win.activateWindow()

    # ── Hotkey ────────────────────────────────────────────────────────────────

    def _register_hotkey(self, hotkey_str: str) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None

        if not pynput_keyboard or not hotkey_str.strip():
            return
        try:
            parsed = _parse_hotkey(hotkey_str)
            self._hotkey_listener = pynput_keyboard.GlobalHotKeys(
                {parsed: lambda: self.stop_requested.emit()}
            )
            self._hotkey_listener.start()
        except Exception as exc:
            print(f"[Hotkey] Could not register '{hotkey_str}': {exc}")

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._web_tracker:
            try:
                self._web_tracker.stop_server()
            except Exception:
                pass
        if self._hotkey_listener:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        super().closeEvent(event)
