from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# pyrefly: ignore [missing-import]
from PySide6.QtCore import Qt, Signal
# pyrefly: ignore [missing-import]
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from analyzer import batch_analyze_sessions
from video_processor import VideoProcessorWindow

try:
    from pynput import keyboard as pynput_keyboard
except Exception:  # pragma: no cover
    pynput_keyboard = None


def parse_hotkey_to_pynput(hotkey_str: str) -> str:
    """Convert human hotkey string like 'Ctrl+Alt+F9' to pynput format '<ctrl>+<alt>+<f9>'."""
    parts = [p.strip().lower() for p in hotkey_str.split("+") if p.strip()]
    parsed_parts = []
    for part in parts:
        if part in ("ctrl", "control"):
            parsed_parts.append("<ctrl>")
        elif part == "alt":
            parsed_parts.append("<alt>")
        elif part == "shift":
            parsed_parts.append("<shift>")
        elif part in ("cmd", "win", "super"):
            parsed_parts.append("<cmd>")
        elif part.startswith("f") and part[1:].isdigit():
            parsed_parts.append(f"<{part}>")
        else:
            parsed_parts.append(part)
    return "+".join(parsed_parts)


class SessionWindow(QWidget):
    stop_requested = Signal()

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self.stop_requested.connect(self.on_stop)
        self._video_processor_window = None

        # Form Controls
        self.participant_input = QLineEdit()
        self.participant_input.setPlaceholderText("e.g. P001")
        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("e.g. Task_1_Checkout")
        self.session_input = QLineEdit()
        self.session_input.setPlaceholderText("e.g. S001")

        self.session_root_input = QLineEdit()
        self.session_root_input.setPlaceholderText("e.g. ./sessions")
        self.session_root_input.setText("sessions")
        
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self._browse_session_root)

        self.fps_input = QSpinBox()
        self.fps_input.setRange(1, 120)
        self.fps_input.setValue(15)

        self.privacy_checkbox = QCheckBox("Enable Keystroke Privacy (log count only)")
        self.privacy_checkbox.setToolTip("Mask specific character key names for privacy compliance.")

        self.hotkey_input = QLineEdit("Ctrl+Alt+F9")
        self.hotkey_input.setToolTip("System-wide hotkey to stop session while window is minimized.")

        # Pupil Labs Eye Tracker Configuration Controls
        self.pupil_mode_combo = QComboBox()
        self.pupil_mode_combo.addItem("Pupil Capture (ZMQ)", "zmq")
        self.pupil_mode_combo.addItem("Pupil Labs Realtime API (Neon/Invisible)", "realtime")
        self.pupil_mode_combo.addItem("Auto-Detect", "auto")
        self.pupil_mode_combo.addItem("Disabled", "disabled")

        self.pupil_ip_input = QLineEdit("127.0.0.1")
        self.pupil_ip_input.setPlaceholderText("e.g. 127.0.0.1 or 192.168.1.100")
        self.pupil_ip_input.setToolTip("IP address of the machine running Pupil Capture or eye tracker.")

        self.pupil_port_input = QSpinBox()
        self.pupil_port_input.setRange(1024, 65535)
        self.pupil_port_input.setValue(50020)
        self.pupil_port_input.setToolTip("ZMQ Port (default 50020 for Pupil Capture Remote API).")

        # Status & Controls
        self.status_label = QLabel("Status: Idle")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #2b2b2b;")

        self.start_button = QPushButton("Start Session")
        self.start_button.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 12px;")
        self.stop_button = QPushButton("Stop Session")
        self.stop_button.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; padding: 6px 12px;")
        self.stop_button.setEnabled(False)

        self.export_button = QPushButton("Export Metrics Summary CSV")
        self.export_button.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; padding: 5px 10px;")

        self.post_process_btn = QPushButton("Burn Clicks into Video (Post-Process)")
        self.post_process_btn.setStyleSheet("background-color: #f57c00; color: white; font-weight: bold; padding: 5px 10px;")

        self._hotkey_listener: Optional[object] = None

        self.start_button.clicked.connect(self.on_start)
        self.stop_button.clicked.connect(self.on_stop)
        self.export_button.clicked.connect(self.on_export_metrics)
        self.post_process_btn.clicked.connect(self._open_video_processor)
        self.hotkey_input.editingFinished.connect(self._register_hotkey_listener)

        # Form Layout
        form = QFormLayout()
        form.addRow("Participant ID *", self.participant_input)
        form.addRow("Task", self.task_input)
        form.addRow("Session #", self.session_input)
        
        root_layout = QHBoxLayout()
        root_layout.addWidget(self.session_root_input)
        root_layout.addWidget(self.browse_button)
        form.addRow("Storage Folder", root_layout)

        form.addRow("Recording FPS", self.fps_input)
        form.addRow("Stop Hotkey", self.hotkey_input)
        form.addRow("Privacy", self.privacy_checkbox)

        # Eye Tracker Settings Group
        eye_group = QGroupBox("Pupil Labs Eye Tracker Settings")
        eye_form = QFormLayout(eye_group)
        eye_form.addRow("Connection Mode", self.pupil_mode_combo)
        eye_form.addRow("Remote IP", self.pupil_ip_input)
        eye_form.addRow("ZMQ Port", self.pupil_port_input)

        btn_box = QHBoxLayout()
        btn_box.addWidget(self.start_button)
        btn_box.addWidget(self.stop_button)

        status_box = QGroupBox("Session Status & Analysis")
        status_layout = QVBoxLayout(status_box)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.export_button)
        status_layout.addWidget(self.post_process_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(eye_group)
        layout.addLayout(btn_box)
        layout.addWidget(status_box)

        self.setWindowTitle("Usability Testing Recorder (Phase 1)")
        self.resize(480, 400)

        self._load_config()
        self._register_hotkey_listener()

    def _open_video_processor(self) -> None:
        if self._video_processor_window is None:
            self._video_processor_window = VideoProcessorWindow()
        self._video_processor_window.show()
        self._video_processor_window.activateWindow()

    def _browse_session_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Session Storage Directory")
        if directory:
            self.session_root_input.setText(directory)

    def on_start(self) -> None:
        participant_id = self.participant_input.text().strip()
        task_label = self.task_input.text().strip()
        session_label = self.session_input.text().strip()
        if not participant_id:
            QMessageBox.warning(self, "Missing Participant ID", "Please enter a valid Participant ID before starting.")
            return

        try:
            fps = self.fps_input.value()
            full_key_logging = not self.privacy_checkbox.isChecked()
            pupil_mode = self.pupil_mode_combo.currentData()
            pupil_ip = self.pupil_ip_input.text().strip() or "127.0.0.1"
            pupil_port = self.pupil_port_input.value()
            
            # Update controller session root
            self.controller.session_root = Path(self.session_root_input.text().strip() or "sessions")

            metadata = self.controller.start_session(
                participant_id,
                task_label,
                session_label,
                fps=fps,
                full_key_logging=full_key_logging,
                pupil_mode=pupil_mode,
                pupil_ip=pupil_ip,
                pupil_port=pupil_port,
            )
            self._save_config()

            pupil_info = metadata.get("pupil_trigger_details", {})
            if pupil_info.get("recording_started"):
                device_status = f"Eye Tracker Connected ({pupil_info.get('serial_number')})"
            else:
                device_status = "Eye Tracker Not Connected (Session Proceeding)"

            self.status_label.setText(f"RECORDING | {device_status}")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #2e7d32;")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)

            self.showMinimized()
        except Exception as exc:
            QMessageBox.critical(self, "Session Startup Error", f"Failed to start recording session:\n{exc}")
            self.status_label.setText("Status: Startup Error")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #c62828;")

    def on_stop(self) -> None:
        if not self.stop_button.isEnabled():
            return
        try:
            summary = self.controller.stop_session()
            metrics = summary.get("analysis_metrics", {})
            duration_str = metrics.get("task_duration_formatted", "N/A")
            clicks = metrics.get("total_clicks", 0)

            self.status_label.setText(f"Stopped | Duration: {duration_str} | Clicks: {clicks}")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #1565c0;")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.showNormal()
            self.activateWindow()

            QMessageBox.information(
                self,
                "Session Completed",
                f"Session stopped cleanly.\nTask Duration: {duration_str}\nTotal Clicks: {clicks}\n\nFiles saved to:\n{self.controller.session_dir}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Session Stop Error", f"Error stopping session components:\n{exc}")
            self.status_label.setText("Status: Stop Error")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #c62828;")

    def on_export_metrics(self) -> None:
        try:
            current_root = self.session_root_input.text().strip() or "sessions"
            output_csv = batch_analyze_sessions(current_root, "sessions_summary.csv")
            QMessageBox.information(
                self,
                "Metrics Exported",
                f"Consolidated usability metrics summary exported successfully to:\n{output_csv}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Failed to export metrics summary CSV:\n{exc}")

    def _register_hotkey_listener(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None

        if pynput_keyboard is None:
            return

        raw_hotkey = self.hotkey_input.text().strip()
        if not raw_hotkey:
            return

        try:
            parsed_hotkey = parse_hotkey_to_pynput(raw_hotkey)
            self._hotkey_listener = pynput_keyboard.GlobalHotKeys(
                {parsed_hotkey: self._on_hotkey_triggered}
            )
            self._hotkey_listener.start()
        except Exception as exc:
            print(f"Warning: Failed to register hotkey '{raw_hotkey}': {exc}")

    def _on_hotkey_triggered(self) -> None:
        self.stop_requested.emit()

    def _save_config(self) -> None:
        config_path = Path("config.json")
        payload = {
            "hotkey": self.hotkey_input.text().strip(),
            "fps": self.fps_input.value(),
            "privacy_mode": self.privacy_checkbox.isChecked(),
            "pupil_mode": self.pupil_mode_combo.currentData(),
            "pupil_ip": self.pupil_ip_input.text().strip(),
            "pupil_port": self.pupil_port_input.value(),
            "session_root": self.session_root_input.text().strip(),
        }
        config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_config(self) -> None:
        config_path = Path("config.json")
        if config_path.exists():
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
                hotkey = payload.get("hotkey", "Ctrl+Alt+F9")
                fps = int(payload.get("fps", 15))
                privacy_mode = bool(payload.get("privacy_mode", False))
                pupil_mode = payload.get("pupil_mode", "zmq")
                pupil_ip = payload.get("pupil_ip", "127.0.0.1")
                pupil_port = int(payload.get("pupil_port", 50020))
                session_root = payload.get("session_root", "sessions")

                self.hotkey_input.setText(hotkey)
                self.fps_input.setValue(max(1, min(120, fps)))
                self.privacy_checkbox.setChecked(privacy_mode)
                self.session_root_input.setText(session_root)

                idx = self.pupil_mode_combo.findData(pupil_mode)
                if idx >= 0:
                    self.pupil_mode_combo.setCurrentIndex(idx)
                self.pupil_ip_input.setText(pupil_ip)
                self.pupil_port_input.setValue(max(1024, min(65535, pupil_port)))
            except Exception:
                self.hotkey_input.setText("Ctrl+Alt+F9")
                self.fps_input.setValue(15)
