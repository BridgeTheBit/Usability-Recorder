"""
gui.py — Minimal desktop recording widget for Usability Testing Recorder.

Only two controls:
  • Start / Stop button  (large, changes color/label while recording)
  • ⚙ Gear button        (opens the settings panel in a native window)

All settings, session management, export, and post-processing live in the
web panel served by settings_server.py.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app_paths import get_default_sessions_dir, get_resource_path
from settings_server import AppState, SettingsServer, app_state, settings_server
from web_tracker import WebTracker

try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None

def _parse_hotkey(hotkey_str: str) -> str:
    if not isinstance(hotkey_str, str) or not hotkey_str.strip():
        raise ValueError("Hotkey cannot be empty.")

    parts = [part.strip().lower() for part in hotkey_str.split("+")]
    if any(not part for part in parts):
        raise ValueError("Use a format such as Ctrl+Alt+F9.")

    modifier_names = {
        "ctrl": "<ctrl>", "control": "<ctrl>", "alt": "<alt>",
        "shift": "<shift>", "cmd": "<cmd>", "win": "<cmd>", "super": "<cmd>",
    }
    modifiers = [modifier_names[part] for part in parts if part in modifier_names]
    keys = [part for part in parts if part not in modifier_names]
    if not modifiers:
        raise ValueError("A hotkey must include at least one modifier, such as Ctrl or Alt.")
    if len(set(modifiers)) != len(modifiers):
        raise ValueError("A modifier can only appear once in a hotkey.")
    if len(keys) != 1:
        raise ValueError("A hotkey must include exactly one key after its modifiers.")

    key = keys[0]
    function_key = re.fullmatch(r"f(\d{1,2})", key)
    if function_key:
        number = int(function_key.group(1))
        if not 1 <= number <= 24:
            raise ValueError("Function keys must be between F1 and F24.")
        key = f"<f{number}>"
    elif len(key) == 1 and key.isprintable() and not key.isspace() and key != "+":
        pass
    else:
        raise ValueError("Use one letter, number, or function key such as F9.")
    return "+".join([*modifiers, key])


class ControlPanelWindow(QMainWindow):
    """Display the local control panel in a regular Windows app window."""

    def __init__(self, port: int) -> None:
        super().__init__()
        self.setWindowTitle("Atlas — Control Panel")
        self.setWindowIcon(QIcon(str(get_resource_path("assets", "atlas-icon.png"))))
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)

        self._view = QWebEngineView(self)
        self.setCentralWidget(self._view)
        self._load_attempts = 0
        self._view.loadFinished.connect(self._on_load_finished)
        self._view.load(QUrl(f"http://127.0.0.1:{port}/"))

    def _on_load_finished(self, success: bool) -> None:
        if not success and self._load_attempts < 3:
            self._load_attempts += 1
            QTimer.singleShot(700, self._view.reload)


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
        self._hotkey_listener = None
        self._configured_hotkey: Optional[str] = None
        self._elapsed_timer: Optional[QTimer] = None
        self._elapsed_seconds = 0
        self._panel_window: Optional[ControlPanelWindow] = None

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
        self.setWindowTitle("Atlas — Usability Recorder")
        self.setFixedSize(360, 130)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(self._WINDOW_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ── Header row ────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        self._logo = QLabel()
        logo_pixmap = QPixmap(str(get_resource_path("assets", "atlas-icon.png")))
        self._logo.setPixmap(logo_pixmap.scaled(
            26, 26,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        hdr.addWidget(self._logo)

        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #484f58; font-size: 11px;")

        self._title = QLabel("Atlas")
        self._title.setStyleSheet("color: #8b949e; font-size: 12px; font-weight: 600;")

        hdr.addWidget(self._dot)
        hdr.addWidget(self._title)
        hdr.addStretch()

        self._gear_btn = QPushButton("⚙")
        self._gear_btn.setFixedSize(32, 32)
        self._gear_btn.setToolTip("Open Atlas Control Panel")
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
        if self._panel_window is None:
            self._panel_window = ControlPanelWindow(self._server.port)
        self._panel_window.showNormal()
        self._panel_window.raise_()
        self._panel_window.activateWindow()

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

            self.controller.session_root = Path(cfg.get("session_root", get_default_sessions_dir()))

            metadata = self.controller.start_session(
                participant_id  = participant_id,
                task_label      = cfg.get("task_label", ""),
                session         = cfg.get("session_label", ""),
                fps             = int(cfg.get("fps", 15)),
                full_key_logging= not cfg.get("privacy_mode", False),
                pupil_mode      = cfg.get("pupil_mode", "disabled"),
                pupil_ip        = cfg.get("pupil_ip", "127.0.0.1"),
                pupil_port      = int(cfg.get("pupil_port", 50020)),
                tracking_mode   = tracking_mode,
                web_tracker_port= web_tracker_port,
                existing_web_tracker = self._web_tracker if tracking_mode == "web" else None,
            )

            session_dir = str(getattr(self.controller, "session_dir", ""))
            self._state.set_recording(True, session_dir, metadata.get("t0_monotonic"))
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
        summary = None
        stop_error = None
        try:
            summary = self.controller.stop_session()
        except Exception as exc:
            stop_error = exc
        finally:
            self._state.set_recording(False)
            self._stop_elapsed_timer()
            self._rec_btn.setStyleSheet(self._BTN_IDLE)
            self._rec_btn.setText("▶   Start Recording")
            self._dot.setStyleSheet("color: #484f58; font-size: 11px;")
            self._title.setStyleSheet("color: #8b949e; font-size: 12px; font-weight: 600;")

        if stop_error is not None:
            self._status.setText("Session stopped with an error")
            self._status.setStyleSheet("color: #f85149; font-size: 11px;")
            QMessageBox.critical(self, "Session Stop Error", str(stop_error))
            return

        self._state.set_last_summary(summary)
        metrics = summary.get("analysis_metrics", {})
        dur = metrics.get("task_duration_formatted", "—")
        clicks = metrics.get("total_clicks", 0)
        self._status.setText(f"Last session: {dur} | {clicks} clicks")
        self._status.setStyleSheet("color: #58a6ff; font-size: 11px;")
        self.showNormal()
        self.activateWindow()
        cleanup_errors = summary.get("cleanup_errors", [])
        if cleanup_errors:
            QMessageBox.warning(self, "Session Saved With Issues", "\n".join(cleanup_errors))

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
        hotkey = self._state.config.get("hotkey", "Ctrl+Alt+F9")
        if hotkey != self._configured_hotkey:
            self._register_hotkey(hotkey)

    # ── Web Tracker ───────────────────────────────────────────────────────────

    def _ensure_web_tracker(self, port: int) -> None:
        if self._web_tracker and self._web_tracker.port == port and self._web_tracker.is_server_running:
            return
        if self._web_tracker:
            try:
                self._web_tracker.stop_server()
            except Exception:
                pass
        tracker = WebTracker(port=port)
        tracker.start_server()
        self._web_tracker = tracker

    def _apply_saved_tracking(self) -> None:
        cfg = self._state.config
        if cfg.get("tracking_mode") == "web":
            try:
                self._ensure_web_tracker(int(cfg.get("web_tracker_port", 9877)))
            except Exception as exc:
                self._status.setText(f"Web tracking unavailable: {exc}")
                self._status.setStyleSheet("color: #d29922; font-size: 11px;")

    # ── Hotkey ────────────────────────────────────────────────────────────────

    def _register_hotkey(self, hotkey_str: str) -> None:
        if not isinstance(hotkey_str, str):
            hotkey_str = ""
        self._configured_hotkey = hotkey_str
        if not hotkey_str.strip():
            self._stop_hotkey_listener()
            if not self._state.is_recording:
                self._status.setText("Stop shortcut disabled — use the Stop button")
                self._status.setToolTip("")
            return
        if not pynput_keyboard:
            print("[Hotkey] Global hotkeys are unavailable because pynput could not be loaded.")
            if not self._state.is_recording:
                self._status.setText("Keyboard shortcut unavailable — use the Stop button")
            return

        try:
            parsed = _parse_hotkey(hotkey_str)
            listener = pynput_keyboard.GlobalHotKeys(
                {parsed: lambda: self.stop_requested.emit()}
            )
            listener.start()
        except Exception as exc:
            print(f"[Hotkey] Could not register '{hotkey_str}': {exc}")
            if not self._state.is_recording:
                self._status.setText("Shortcut not applied — the previous shortcut is still active")
                self._status.setToolTip(str(exc))
            return

        previous_listener = self._hotkey_listener
        self._hotkey_listener = listener
        if previous_listener is not None:
            try:
                previous_listener.stop()
                previous_listener.join(timeout=1.0)
            except Exception:
                pass
        if not self._state.is_recording and self._status.text().startswith((
            "Shortcut not applied", "Stop shortcut disabled", "Keyboard shortcut unavailable"
        )):
            self._status.setText("Ready — open ⚙ to configure")
            self._status.setToolTip("")

    def _stop_hotkey_listener(self) -> None:
        listener = self._hotkey_listener
        self._hotkey_listener = None
        if listener is not None:
            try:
                listener.stop()
                listener.join(timeout=1.0)
            except Exception:
                pass

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        if self._panel_window is not None:
            self._panel_window.close()
        stop_error = None
        if self._state.is_recording:
            try:
                summary = self.controller.stop_session()
                self._state.set_last_summary(summary)
            except Exception as exc:
                stop_error = exc
            finally:
                self._state.set_recording(False)
                self._stop_elapsed_timer()
        if self._web_tracker:
            try:
                self._web_tracker.stop_server()
            except Exception:
                pass
        self._stop_hotkey_listener()
        if stop_error is not None:
            QMessageBox.critical(self, "Session Stop Error", str(stop_error))
        super().closeEvent(event)
