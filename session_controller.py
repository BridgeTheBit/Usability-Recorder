from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from app_paths import get_app_data_dir, get_default_sessions_dir
from analyzer import SessionAnalyzer, batch_analyze_sessions
from input_logger import InputLogger
from pupil_trigger import PupilTrigger
from screen_recorder import ScreenRecorder
from window_tracker import WindowTracker
from web_tracker import WebTracker


class SessionController:
    """
    Coordinates the lifecycle of a usability testing session.
    Enforces a single shared monotonic clock t0 for all recorded streams.
    Automatically computes analytical metrics (task duration, click counts) upon session stop.
    """

    def __init__(self, session_root: str | Path | None = None) -> None:
        self.session_root = Path(session_root) if session_root is not None else get_default_sessions_dir()
        self.t0: Optional[float] = None
        self.input_logger: Optional[InputLogger] = None
        self.screen_recorder: Optional[ScreenRecorder] = None
        self.pupil_trigger: Optional[PupilTrigger] = None
        self.window_tracker: Optional[WindowTracker] = None
        self.web_tracker: Optional[WebTracker] = None
        self.session_dir: Optional[Path] = None
        self.metadata: dict = {}
        self.logger: Optional[logging.Logger] = None
        self._session_active = False
        self._owns_web_tracker_server = False

    def start_session(
        self,
        participant_id: str,
        task_label: str = "",
        session: str = "",
        fps: int = 15,
        full_key_logging: bool = True,
        pupil_mode: str = "disabled",
        pupil_ip: str = "127.0.0.1",
        pupil_port: int = 50020,
        tracking_mode: str = "disabled",
        web_tracker_port: int = 9877,
        existing_web_tracker: Optional["WebTracker"] = None,
    ) -> dict:
        """Validate settings and roll back every started module on partial failure."""
        if self._session_active:
            raise RuntimeError("A recording session is already active.")
        if not isinstance(participant_id, str) or not participant_id.strip():
            raise ValueError("Participant ID is required.")
        if type(fps) is not int or not 1 <= fps <= 120:
            raise ValueError("FPS must be an integer between 1 and 120.")
        if tracking_mode not in {"disabled", "windows", "web"}:
            raise ValueError("Tracking mode must be disabled, windows, or web.")
        if pupil_mode not in {"disabled", "zmq", "realtime", "auto"}:
            raise ValueError("Pupil mode must be disabled, zmq, realtime, or auto.")
        if tracking_mode == "web" and not 1024 <= int(web_tracker_port) <= 65535:
            raise ValueError("Web tracker port must be between 1024 and 65535.")
        if pupil_mode != "disabled" and not 1 <= int(pupil_port) <= 65535:
            raise ValueError("Pupil port must be between 1 and 65535.")

        try:
            result = self._start_session_impl(
                participant_id=participant_id,
                task_label=task_label,
                session=session,
                fps=fps,
                full_key_logging=full_key_logging,
                pupil_mode=pupil_mode,
                pupil_ip=pupil_ip,
                pupil_port=pupil_port,
                tracking_mode=tracking_mode,
                web_tracker_port=int(web_tracker_port),
                existing_web_tracker=existing_web_tracker,
            )
            self._session_active = True
            return result
        except Exception as exc:
            self._rollback_startup(exc)
            raise

    def _start_session_impl(
        self,
        participant_id: str,
        task_label: str = "",
        session: str = "",
        fps: int = 15,
        full_key_logging: bool = True,
        pupil_mode: str = "disabled",
        pupil_ip: str = "127.0.0.1",
        pupil_port: int = 50020,
        tracking_mode: str = "disabled",
        web_tracker_port: int = 9877,
        existing_web_tracker: Optional["WebTracker"] = None,
    ) -> dict:
        """
        Initialize session directory, capture t0 monotonic reference, and launch all recording streams.
        """
        self.input_logger = None
        self.screen_recorder = None
        self.pupil_trigger = None
        self.window_tracker = None
        self.web_tracker = None
        self._owns_web_tracker_server = False
        self.session_dir = None
        self.t0 = None
        self.metadata = {}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_id = "".join(c for c in participant_id if c.isalnum() or c in ("-", "_")).strip() or "P_UNKNOWN"
        self.session_root.mkdir(parents=True, exist_ok=True)
        base_name = f"{safe_id}_{timestamp}"
        self.session_dir = self.session_root / base_name
        suffix = 1
        while self.session_dir.exists():
            self.session_dir = self.session_root / f"{base_name}_{suffix:02d}"
            suffix += 1
        self.session_dir.mkdir(parents=False, exist_ok=False)

        self._setup_logger()

        # SHARED REFERENCE CLOCK: Capture t0 at the exact moment session starts
        self.t0 = time.monotonic()
        self.logger.info("Session started. Shared monotonic reference clock t0 = %.6f", self.t0)

        self.metadata = {
            "participant_id": participant_id,
            "task_label": task_label,
            "session": session,
            "session_label": session,
            "session_start_wall_clock": datetime.now().isoformat(),
            "session_end_wall_clock": None,
            "t0_monotonic": self.t0,
            "fps": fps,
            "full_key_logging": full_key_logging,
            "screen_first_frame_timestamp_s": None,
            "screen_capture_backend": None,
            "pupil_mode": pupil_mode,
            "pupil_ip": pupil_ip,
            "pupil_port": pupil_port,
            "pupil_offset_estimate_s": None,
            "device_serial_number": None,
            "tracking_mode": tracking_mode,
            "web_tracker_port": web_tracker_port,
            "output_files": [],
        }
        self._write_metadata()

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
        self.metadata["screen_capture_backend"] = self.screen_recorder.backend
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

        # 4. Window / Web Tracker
        window_switches_path = self.session_dir / "window_switches.csv"
        web_visits_path = self.session_dir / "web_page_visits.csv"

        if tracking_mode == "windows":
            self.window_tracker = WindowTracker(str(window_switches_path))
            self.window_tracker.start(self.t0)
            self.logger.info("Window tracker started -> window_switches.csv")
            self.metadata["output_files"].append(str(window_switches_path.resolve()))
        elif tracking_mode == "web":
            if existing_web_tracker is not None:
                # Use the already-running persistent server from the GUI
                self.web_tracker = existing_web_tracker
                self.web_tracker.start_recording(self.t0, str(web_visits_path))
                self.logger.info(
                    "Web tracker recording started (existing server on port %d) -> web_page_visits.csv",
                    existing_web_tracker.port,
                )
            else:
                # Fallback: create a new server (legacy / standalone usage)
                self.web_tracker = WebTracker(port=web_tracker_port, logger=self.logger)
                self._owns_web_tracker_server = True
                self.web_tracker.start_server()
                self.web_tracker.start_recording(self.t0, str(web_visits_path))
                self.logger.info(
                    "Web tracker server + recording started on port %d -> web_page_visits.csv",
                    web_tracker_port,
                )
            self.metadata["output_files"].append(str(web_visits_path.resolve()))
        else:
            self.logger.info("Window/Web tracking disabled.")

        self.metadata["output_files"] += [
            str(events_path.resolve()),
            str(screen_path.resolve()),
            str((self.session_dir / "session_metadata.json").resolve()),
            str((self.session_dir / "session.log").resolve()),
        ]
        self._write_metadata()
        return self.metadata

    def stop_session(self) -> dict:
        """Stop every module, retain cleanup errors, and finalize session metadata."""
        if not self._session_active:
            return {}
        self._session_active = False
        cleanup_errors: list[str] = []
        pupil_meta: dict = {}

        if self.logger is not None:
            self.logger.info("Initiating session shutdown sequence...")

        def attempt(label: str, callback):
            try:
                return callback()
            except Exception as exc:
                cleanup_errors.append(f"{label}: {exc}")
                if self.logger is not None:
                    self.logger.exception("Could not stop %s cleanly.", label)
                return None

        if self.input_logger is not None:
            attempt("input logger", self.input_logger.stop)
        if self.screen_recorder is not None:
            attempt("screen recorder", self.screen_recorder.stop)

        if self.window_tracker is not None:
            window_summary = attempt("window tracker", self.window_tracker.stop) or {}
            self.metadata["window_switch_summary"] = {
                "total_window_switches": window_summary.get("total_window_switches", 0),
                "unique_windows_visited": window_summary.get("unique_windows_visited", 0),
                "unique_window_names": window_summary.get("unique_window_names", []),
            }

        if self.web_tracker is not None:
            web_summary = attempt("web tracking", self.web_tracker.stop_recording) or {}
            self.metadata["web_visit_summary"] = {
                "total_page_visits": web_summary.get("total_page_visits", 0),
                "unique_urls_visited": web_summary.get("unique_urls_visited", 0),
                "unique_domains_visited": web_summary.get("unique_domains_visited", 0),
                "unique_domain_names": web_summary.get("unique_domain_names", []),
            }
            if self._owns_web_tracker_server:
                attempt("web tracker server", self.web_tracker.stop_server)

        if self.pupil_trigger is not None:
            pupil_meta = attempt(
                "Pupil Labs trigger",
                lambda: self.pupil_trigger.stop_session(
                    self.t0 if self.t0 is not None else time.monotonic()
                ),
            ) or {}
            self.metadata["pupil_offset_estimate_s"] = self.pupil_trigger.last_offset_estimate
            self.metadata["device_serial_number"] = self.pupil_trigger.serial_number
            self.metadata["pupil_stop_details"] = pupil_meta

        self.metadata["session_end_wall_clock"] = datetime.now().isoformat()
        self.metadata["cleanup_errors"] = cleanup_errors

        try:
            if self.session_dir is not None:
                metrics = SessionAnalyzer(self.session_dir).analyze()
                self.metadata["analysis_metrics"] = metrics
                batch_analyze_sessions(self.session_root, get_app_data_dir() / "sessions_summary.csv")
        except Exception as exc:
            cleanup_errors.append(f"session analysis: {exc}")
            self.metadata["cleanup_errors"] = cleanup_errors
            if self.logger is not None:
                self.logger.exception("Could not analyze the completed session.")

        try:
            self._write_metadata()
        except Exception as exc:
            cleanup_errors.append(f"metadata write: {exc}")
            self.metadata["cleanup_errors"] = cleanup_errors
            if self.logger is not None:
                self.logger.exception("Could not write final session metadata.")

        if self.logger is not None:
            self.logger.info("Session shutdown completed with %d cleanup issue(s).", len(cleanup_errors))
            for handler in list(self.logger.handlers):
                try:
                    handler.flush()
                    handler.close()
                finally:
                    self.logger.removeHandler(handler)
            self.logger = None

        return {**self.metadata, **pupil_meta}

    def _rollback_startup(self, cause: Exception) -> None:
        """Stop modules that started before a later startup step failed."""
        errors: list[str] = []
        if self.logger is not None:
            self.logger.error(
                "Session startup failed; rolling back started modules.",
                exc_info=(type(cause), cause, cause.__traceback__),
            )

        def attempt(label: str, callback) -> None:
            try:
                callback()
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                if self.logger is not None:
                    self.logger.exception("Startup rollback could not stop %s.", label)

        if self.input_logger is not None:
            attempt("input logger", self.input_logger.stop)
        if self.screen_recorder is not None:
            attempt("screen recorder", self.screen_recorder.stop)
        if self.window_tracker is not None:
            attempt("window tracker", self.window_tracker.stop)
        if self.web_tracker is not None:
            if getattr(self.web_tracker, "_recording", False):
                attempt("web tracking", self.web_tracker.stop_recording)
            if self._owns_web_tracker_server:
                attempt("web tracker server", self.web_tracker.stop_server)
        if self.pupil_trigger is not None:
            attempt(
                "Pupil Labs trigger",
                lambda: self.pupil_trigger.stop_session(
                    self.t0 if self.t0 is not None else time.monotonic()
                ),
            )

        self._session_active = False
        self.metadata["session_start_error"] = str(cause)
        self.metadata["startup_rollback_errors"] = errors
        self.metadata["session_end_wall_clock"] = datetime.now().isoformat()
        if self.session_dir is not None and self.session_dir.exists():
            self.metadata["output_files"] = [
                str(path.resolve()) for path in self.session_dir.iterdir() if path.is_file()
            ]
            try:
                self._write_metadata()
            except Exception:
                if self.logger is not None:
                    self.logger.exception("Could not save metadata for the failed startup.")

        if self.logger is not None:
            for handler in list(self.logger.handlers):
                try:
                    handler.flush()
                    handler.close()
                finally:
                    self.logger.removeHandler(handler)
            self.logger = None

    def _setup_logger(self) -> None:
        if self.session_dir is None:
            raise RuntimeError("Session directory not initialized")
        logging_path = self.session_dir / "session.log"
        self.logger = logging.getLogger(f"session_{self.session_dir.name}")
        self.logger.setLevel(logging.INFO)
        for old_handler in list(self.logger.handlers):
            old_handler.close()
            self.logger.removeHandler(old_handler)
        handler = logging.FileHandler(logging_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self.logger.addHandler(handler)

    def _write_metadata(self) -> None:
        if self.session_dir is None:
            return
        metadata_path = self.session_dir / "session_metadata.json"
        temporary_path = metadata_path.with_suffix(".json.tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(self.metadata, handle, indent=2)
        temporary_path.replace(metadata_path)
