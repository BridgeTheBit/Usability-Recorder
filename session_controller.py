from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from analyzer import SessionAnalyzer, batch_analyze_sessions
from input_logger import InputLogger
from pupil_trigger import PupilTrigger
from screen_recorder import ScreenRecorder


class SessionController:
    """
    Coordinates the lifecycle of a usability testing session.
    Enforces a single shared monotonic clock t0 for all recorded streams.
    Automatically computes analytical metrics (task duration, click counts) upon session stop.
    """

    def __init__(self, session_root: str = "sessions") -> None:
        self.session_root = Path(session_root)
        self.t0: Optional[float] = None
        self.input_logger: Optional[InputLogger] = None
        self.screen_recorder: Optional[ScreenRecorder] = None
        self.pupil_trigger: Optional[PupilTrigger] = None
        self.session_dir: Optional[Path] = None
        self.metadata: dict = {}
        self.logger: Optional[logging.Logger] = None

    def start_session(
        self,
        participant_id: str,
        task_label: str = "",
        session: str = "",
        fps: int = 15,
        full_key_logging: bool = True,
        pupil_mode: str = "zmq",
        pupil_ip: str = "127.0.0.1",
        pupil_port: int = 50020,
    ) -> dict:
        """
        Initialize session directory, capture t0 monotonic reference, and launch all recording streams.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_id = "".join(c for c in participant_id if c.isalnum() or c in ("-", "_")).strip() or "P_UNKNOWN"
        self.session_dir = self.session_root / f"{safe_id}_{timestamp}"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self._setup_logger()

        # SHARED REFERENCE CLOCK: Capture t0 at the exact moment session starts
        self.t0 = time.monotonic()
        self.logger.info("Session started. Shared monotonic reference clock t0 = %.6f", self.t0)

        self.metadata = {
            "participant_id": participant_id,
            "task_label": task_label,
            "session": session,
            "session_start_wall_clock": datetime.now().isoformat(),
            "session_end_wall_clock": None,
            "t0_monotonic": self.t0,
            "fps": fps,
            "full_key_logging": full_key_logging,
            "screen_first_frame_timestamp_s": None,
            "pupil_mode": pupil_mode,
            "pupil_ip": pupil_ip,
            "pupil_port": pupil_port,
            "pupil_offset_estimate_s": None,
            "device_serial_number": None,
            "output_files": [],
        }

        # 1. Input Logger
        events_path = self.session_dir / "events.csv"
        self.input_logger = InputLogger(
            str(events_path),
            full_key_logging=full_key_logging,
            mouse_move_rate_hz=float(fps),
        )
        self.input_logger.start(self.t0)
        self.logger.info("Input logger started -> events.csv (full_key_logging=%s)", full_key_logging)

        # 2. Screen Recorder
        screen_path = self.session_dir / "screen_recording.mp4"
        self.screen_recorder = ScreenRecorder(str(screen_path), fps=fps)
        self.screen_recorder.start(self.t0)
        first_frame_ts = self.screen_recorder.get_first_frame_timestamp_s()
        self.metadata["screen_first_frame_timestamp_s"] = first_frame_ts
        self.logger.info("Screen recorder started -> screen_recording.mp4 (first_frame_ts=%.6fs)", first_frame_ts or 0.0)

        # 3. Eye Tracker Trigger (Pupil Capture ZMQ / Realtime API)
        self.pupil_trigger = PupilTrigger(logger=self.logger)
        pupil_meta = self.pupil_trigger.start_session(
            self.t0,
            mode=pupil_mode,
            ip=pupil_ip,
            port=pupil_port,
            session_name=f"{safe_id}_{timestamp}",
            timeout_seconds=3.0,
        )
        self.metadata["pupil_offset_estimate_s"] = pupil_meta.get("offset_estimate_s")
        self.metadata["device_serial_number"] = pupil_meta.get("serial_number")
        self.metadata["pupil_trigger_details"] = pupil_meta

        self.metadata["output_files"] = [
            str(events_path.resolve()),
            str(screen_path.resolve()),
        ]
        self._write_metadata()
        return self.metadata

    def stop_session(self) -> dict:
        """Gracefully stop all recording modules, calculate analytical metrics, and write final metadata."""
        if self.logger is not None:
            self.logger.info("Initiating session shutdown sequence...")

        if self.input_logger is not None:
            self.input_logger.stop()
            if self.logger is not None:
                self.logger.info("Input logger stopped.")

        if self.screen_recorder is not None:
            self.screen_recorder.stop()
            if self.logger is not None:
                self.logger.info("Screen recorder stopped and file finalized.")

        pupil_meta = {}
        if self.pupil_trigger is not None:
            pupil_meta = self.pupil_trigger.stop_session(self.t0 or time.monotonic())
            self.metadata["pupil_offset_estimate_s"] = self.pupil_trigger.last_offset_estimate
            self.metadata["device_serial_number"] = self.pupil_trigger.serial_number
            self.metadata["pupil_stop_details"] = pupil_meta

        self.metadata["session_end_wall_clock"] = datetime.now().isoformat()
        self._write_metadata()

        # Run SessionAnalyzer to calculate task duration, click counts, click rate, etc.
        try:
            if self.session_dir is not None:
                analyzer = SessionAnalyzer(self.session_dir)
                metrics = analyzer.analyze()
                self.metadata["analysis_metrics"] = metrics
                self._write_metadata()
                batch_analyze_sessions(self.session_root, "sessions_summary.csv")
                if self.logger:
                    self.logger.info("Analytical metrics calculated successfully (duration=%.2fs, clicks=%d)",
                                     metrics.get("task_duration_seconds", 0), metrics.get("total_clicks", 0))
        except Exception as exc:
            if self.logger:
                self.logger.warning("Failed to auto-run SessionAnalyzer: %s", exc)

        if self.logger is not None:
            self.logger.info("Session shutdown completed successfully.")

        return {**self.metadata, **pupil_meta}

    def _setup_logger(self) -> None:
        if self.session_dir is None:
            raise RuntimeError("Session directory not initialized")
        logging_path = self.session_dir / "session.log"
        self.logger = logging.getLogger(f"session_{self.session_dir.name}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        handler = logging.FileHandler(logging_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self.logger.addHandler(handler)

    def _write_metadata(self) -> None:
        if self.session_dir is None:
            return
        metadata_path = self.session_dir / "session_metadata.json"
        with metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(self.metadata, handle, indent=2)
