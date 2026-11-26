import csv
import json
import os
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from ffmpeg_utils import require_ffmpeg

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

# pyrefly: ignore [missing-import]
from PySide6.QtCore import Qt, QThread, Signal, QObject
# pyrefly: ignore [missing-import]
from PySide6.QtGui import QImage, QPixmap
# pyrefly: ignore [missing-import]
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QProgressBar, QFileDialog, QMessageBox, QApplication
)


class VideoProcessorThread(QThread):
    progress = Signal(int)
    frame_ready = Signal(QImage)
    status = Signal(str)
    finished_processing = Signal(bool, str)

    def __init__(self, session_dir: Path):
        super().__init__()
        self.session_dir = session_dir
        self._is_running = True

    def run(self):
        try:
            self._process()
        except Exception as e:
            self.finished_processing.emit(False, str(e))

    def stop(self):
        self._is_running = False

    def _process(self):
        if cv2 is None:
            self.finished_processing.emit(False, "OpenCV is required (pip install opencv-python).")
            return

        events_csv = self.session_dir / "events.csv"
        original_video = self.session_dir / "screen_recording.mp4"
        temp_video = self.session_dir / "temp_video_no_audio.mp4"
        final_video = self.session_dir / "screen_recording_with_clicks.mp4"

        if not events_csv.exists() or not original_video.exists():
            self.finished_processing.emit(False, "Missing events.csv or screen_recording.mp4")
            return

        self.status.emit("Parsing click events...")
        metadata_path = self.session_dir / "session_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        offset = float(metadata.get("screen_first_frame_timestamp_s") or 0)
        clicks = self._parse_clicks(events_csv)
        clicks = [{**c, "start": c["start"] - offset, "end": c["end"] - offset}
                  for c in clicks if c["start"] >= offset]
        
        self.status.emit("Processing video frames...")
        cap = cv2.VideoCapture(str(original_video))
        if not cap.isOpened():
            self.finished_processing.emit(False, "Could not open video file.")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(str(temp_video), fourcc, fps, (width, height))

        frame_idx = 0
        while cap.isOpened() and self._is_running:
            ret, frame = cap.read()
            if not ret:
                break
                
            current_time_s = frame_idx / fps
            
            # Draw active clicks
            active_clicks = [c for c in clicks if c['start'] <= current_time_s <= c['end']]
            for c in active_clicks:
                life = (current_time_s - c['start']) / (c['end'] - c['start'])
                alpha = max(0, 1.0 - life)
                
                if 'left' in c['button'].lower():
                    color = (0, 0, int(255 * alpha))
                    text = "Left Click"
                elif 'right' in c['button'].lower():
                    color = (int(40 * alpha), int(235 * alpha), int(245 * alpha))
                    text = "Right Click"
                else:
                    color = (0, int(255 * alpha), 0)
                    text = "Middle Click"
                
                x, y = c['x'], c['y']
                radius = 10 + int(life * 20)
                
                cv2.circle(frame, (x, y), radius, color, 3)
                cv2.putText(frame, text, (x + 15, y - 15), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
                
            out.write(frame)
            
            # Emit preview frame
            if frame_idx % 3 == 0 or active_clicks:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_frame.shape
                bytes_per_line = ch * w
                qimg = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                self.frame_ready.emit(qimg.copy())
            
            frame_idx += 1
            if total_frames > 0 and frame_idx % 10 == 0:
                self.progress.emit(int((frame_idx / total_frames) * 90))

        cap.release()
        out.release()
        
        if not self._is_running:
            if temp_video.exists():
                temp_video.unlink()
            self.finished_processing.emit(False, "Processing cancelled by user.")
            return

        self.status.emit("Muxing audio with FFmpeg...")
        self.progress.emit(95)
        try:
            command = [
                require_ffmpeg(), "-y",
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
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if temp_video.exists():
                temp_video.unlink()
            self.progress.emit(100)
            self.finished_processing.emit(True, f"Video saved to:\n{final_video.name}")
        except Exception as e:
            self.finished_processing.emit(False, f"FFmpeg failed: {e}")


    def _parse_clicks(self, events_csv: Path) -> List[Dict[str, Any]]:
        clicks = []
        try:
            with events_csv.open('r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['event_type'] == 'mouse_click':
                        detail = json.loads(row['detail'])
                        if detail.get('pressed'):
                            ts = float(row['timestamp_s'])
                            clicks.append({
                                'start': ts,
                                'end': ts + 0.8,
                                'x': detail.get('x', 0),
                                'y': detail.get('y', 0),
                                'button': detail.get('button', '')
                            })
        except Exception:
            pass
        return clicks


class VideoProcessorWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Click Overlay Processor")
        self.resize(800, 600)
        self.thread: Optional[VideoProcessorThread] = None
        
        layout = QVBoxLayout(self)
        
        folder_layout = QHBoxLayout()
        self.folder_label = QLabel("No folder selected")
        self.browse_btn = QPushButton("Select Session Folder")
        self.browse_btn.clicked.connect(self._browse)
        folder_layout.addWidget(self.folder_label)
        folder_layout.addWidget(self.browse_btn)
        
        layout.addLayout(folder_layout)
        
        self.start_btn = QPushButton("Start Processing")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._toggle_processing)
        self.start_btn.setStyleSheet("font-weight: bold; padding: 10px;")
        layout.addWidget(self.start_btn)
        
        self.status_label = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        
        self.preview_label = QLabel("Live Preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("background-color: black; color: white;")
        self.preview_label.setMinimumSize(640, 360)
        layout.addWidget(self.preview_label, stretch=1)
        
        self.session_dir: Optional[Path] = None

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Session Folder")
        if d:
            self.session_dir = Path(d)
            self.folder_label.setText(str(self.session_dir))
            
            if (self.session_dir / "events.csv").exists() and (self.session_dir / "screen_recording.mp4").exists():
                self.start_btn.setEnabled(True)
                self.status_label.setText("Ready to process.")
            else:
                self.start_btn.setEnabled(False)
                self.status_label.setText("Error: Selected folder missing events.csv or screen_recording.mp4!")

    def _toggle_processing(self):
        if self.thread is not None and self.thread.isRunning():
            self.thread.stop()
            self.start_btn.setText("Cancelling...")
            self.start_btn.setEnabled(False)
        else:
            self._start_processing()

    def _start_processing(self):
        if not self.session_dir: return
        self.start_btn.setText("Cancel Processing")
        self.start_btn.setStyleSheet("font-weight: bold; padding: 10px; background-color: #c62828; color: white;")
        self.progress_bar.setValue(0)
        self.preview_label.setText("Initializing...")
        
        self.thread = VideoProcessorThread(self.session_dir)
        self.thread.progress.connect(self.progress_bar.setValue)
        self.thread.status.connect(self.status_label.setText)
        self.thread.frame_ready.connect(self._update_preview)
        self.thread.finished_processing.connect(self._on_finished)
        self.thread.start()

    def _update_preview(self, qimg: QImage):
        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

    def _on_finished(self, success: bool, msg: str):
        self.start_btn.setText("Start Processing")
        self.start_btn.setEnabled(True)
        self.start_btn.setStyleSheet("font-weight: bold; padding: 10px;")
        self.status_label.setText(msg)
        
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.warning(self, "Error", msg)
