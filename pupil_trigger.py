from __future__ import annotations

import logging
import time
from typing import Any, Optional

try:
    import zmq
except ImportError:  # pragma: no cover
    zmq = None

try:
    from pupil_labs.realtime_api import discover_one_device
except ImportError:  # pragma: no cover
    discover_one_device = None


class PupilTrigger:
    """
    Eye tracker recording trigger supporting:
    1. Pupil Capture (Pupil Core) via ZMQ Remote API (default tcp://127.0.0.1:50020).
    2. Pupil Labs Realtime API for Neon / Pupil Invisible devices.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("pupil_trigger")
        self.device: Optional[Any] = None
        self.zmq_socket: Optional[Any] = None
        self.zmq_context: Optional[Any] = None
        self.active_mode: str = "disabled"
        self.last_offset_estimate: Optional[float] = None
        self.last_start_ts_s: Optional[float] = None
        self.last_stop_ts_s: Optional[float] = None
        self.serial_number: Optional[str] = None

    def start_session(
        self,
        t0: float,
        mode: str = "zmq",
        ip: str = "127.0.0.1",
        port: int = 50020,
        session_name: str = "",
        timeout_seconds: float = 3.0,
    ) -> dict:
        """
        Discover or connect to Pupil Labs device/software, estimate clock offset, and trigger recording start.
        """
        metadata = {
            "device_found": False,
            "recording_started": False,
            "mode": mode,
            "ip": ip,
            "port": port,
            "offset_estimate_s": None,
            "serial_number": None,
            "trigger_timestamp_s": None,
            "response": None,
        }

        if mode == "disabled":
            self.logger.info("Pupil Labs trigger is disabled in session configuration.")
            return metadata

        # Attempt ZMQ connection first if mode is "zmq" or "auto"
        if mode in ("zmq", "auto"):
            zmq_success = self._start_zmq_session(
                t0, ip, port, session_name, timeout_seconds, metadata
            )
            if zmq_success:
                return metadata
            if mode == "zmq":
                return metadata

        # Attempt Realtime API if mode is "realtime" or "auto"
        if mode in ("realtime", "auto"):
            self._start_realtime_session(t0, timeout_seconds, metadata)

        return metadata

    def _start_zmq_session(
        self,
        t0: float,
        ip: str,
        port: int,
        session_name: str,
        timeout_seconds: float,
        metadata: dict,
    ) -> bool:
        if zmq is None:
            self.logger.warning("pyzmq is not installed; cannot connect to Pupil Capture via ZMQ.")
            return False

        address = f"tcp://{ip}:{port}"
        self.logger.info("Connecting to Pupil Capture via ZMQ at %s...", address)

        try:
            ctx = zmq.Context()
            sock = ctx.socket(zmq.REQ)
            timeout_ms = int(timeout_seconds * 1000)
            sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
            sock.setsockopt(zmq.SNDTIMEO, timeout_ms)
            sock.setsockopt(zmq.LINGER, 0)
            sock.connect(address)

            # Test connection & estimate clock offset by requesting Pupil time ('t')
            t_send = time.monotonic()
            sock.send_string("t")
            reply_time_str = sock.recv_string()
            t_recv = time.monotonic()

            try:
                pupil_clock = float(reply_time_str)
                host_mid_time = (t_send + t_recv) / 2.0
                self.last_offset_estimate = pupil_clock - host_mid_time
                metadata["offset_estimate_s"] = self.last_offset_estimate
                self.logger.info("Pupil Capture connected. Estimated clock offset: %.6fs", self.last_offset_estimate)
            except ValueError:
                self.logger.info("Pupil Capture responded to time query: %s", reply_time_str)

            # Send recording start command 'R' or 'R session_name'
            cmd = f"R {session_name}".strip() if session_name else "R"
            self.last_start_ts_s = time.monotonic() - t0
            sock.send_string(cmd)
            start_reply = sock.recv_string()

            self.zmq_socket = sock
            self.zmq_context = ctx
            self.active_mode = "zmq"
            self.serial_number = f"PupilCapture_{ip}:{port}"

            metadata["device_found"] = True
            metadata["recording_started"] = True
            metadata["serial_number"] = self.serial_number
            metadata["trigger_timestamp_s"] = self.last_start_ts_s
            metadata["response"] = start_reply
            self.logger.info(
                "Pupil Capture ZMQ recording started successfully (cmd='%s', response='%s') at t=%.6fs",
                cmd, start_reply, self.last_start_ts_s
            )
            return True

        except Exception as exc:
            self.logger.warning("Pupil Capture ZMQ remote control connection failed at %s: %s", address, exc)
            self._cleanup_zmq()
            return False

    def _start_realtime_session(self, t0: float, timeout_seconds: float, metadata: dict) -> None:
        if discover_one_device is None:
            self.logger.warning("pupil-labs-realtime-api package not importable; skipping Realtime API discovery.")
            return

        try:
            self.logger.info("Searching for Pupil Labs Neon/Invisible device on network (timeout=%ss)...", timeout_seconds)
            self.device = discover_one_device(timeout=timeout_seconds)
        except Exception as exc:
            self.logger.warning("Pupil Labs Realtime API device discovery failed: %s", exc)
            return

        if self.device is None:
            self.logger.warning("No Pupil Labs Realtime API device found on local network.")
            return

        metadata["device_found"] = True
        self.serial_number = getattr(self.device, "serial_number", None)
        metadata["serial_number"] = self.serial_number

        try:
            estimate_clock_offset = getattr(self.device, "estimate_clock_offset", None)
            if callable(estimate_clock_offset):
                self.last_offset_estimate = estimate_clock_offset()
                metadata["offset_estimate_s"] = self.last_offset_estimate
        except Exception as exc:
            self.logger.warning("Could not estimate clock offset from device: %s", exc)

        try:
            self.last_start_ts_s = time.monotonic() - t0
            metadata["trigger_timestamp_s"] = self.last_start_ts_s
            self.device.recording_start()
            metadata["recording_started"] = True
            self.active_mode = "realtime"
            self.logger.info("Pupil Labs Realtime API recording started at t=%.6fs", self.last_start_ts_s)
        except Exception as exc:
            self.logger.warning("Pupil Labs Realtime API recording_start() failed: %s", exc)

    def stop_session(self, t0: float) -> dict:
        """Send recording stop command to Pupil Capture ZMQ socket or Realtime API device."""
        metadata = {"recording_stopped": False, "stop_timestamp_s": None, "response": None}

        self.last_stop_ts_s = time.monotonic() - t0
        metadata["stop_timestamp_s"] = self.last_stop_ts_s

        if self.active_mode == "zmq" and self.zmq_socket is not None:
            try:
                self.logger.info("Sending 'r' (stop recording) command to Pupil Capture ZMQ...")
                self.zmq_socket.send_string("r")
                stop_reply = self.zmq_socket.recv_string()
                metadata["recording_stopped"] = True
                metadata["response"] = stop_reply
                self.logger.info("Pupil Capture ZMQ recording stopped (response='%s') at t=%.6fs", stop_reply, self.last_stop_ts_s)
            except Exception as exc:
                self.logger.warning("Failed to send stop command to Pupil Capture ZMQ: %s", exc)
            finally:
                self._cleanup_zmq()

        elif self.active_mode == "realtime" and self.device is not None:
            try:
                self.device.recording_stop_and_save()
                metadata["recording_stopped"] = True
                self.logger.info("Pupil Labs Realtime API recording stopped at t=%.6fs", self.last_stop_ts_s)
            except Exception as exc:
                self.logger.warning("Pupil Labs Realtime API recording_stop_and_save() failed: %s", exc)
            finally:
                try:
                    self.device.close()
                except Exception:
                    pass
                self.device = None

        self.active_mode = "disabled"
        return metadata

    def _cleanup_zmq(self) -> None:
        if self.zmq_socket is not None:
            try:
                self.zmq_socket.close()
            except Exception:
                pass
            self.zmq_socket = None
        if self.zmq_context is not None:
            try:
                self.zmq_context.term()
            except Exception:
                pass
            self.zmq_context = None
