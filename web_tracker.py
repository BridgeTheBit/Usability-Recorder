from __future__ import annotations

"""
WebTracker — collects rich browser page-navigation events via a local WebSocket server.

Lifecycle
---------
The WebSocket server lifecycle is SEPARATE from the recording lifecycle:

  1. start_server(port)          → always-on WS server starts (call once when app opens)
  2. start_recording(t0, path)   → activate recording; events will be written to CSV
  3. stop_recording()            → deactivate recording; returns summary stats
  4. stop_server()               → shut down the WS server (call when app closes)

This means the browser extension can connect / stay connected even BEFORE a session
starts.  Events received while not recording are silently discarded.

Expected message schema from the extension
-------------------------------------------
{
  "event":            "page_visit" | "tab_switch" | "page_update",
  "url":              "https://example.com/checkout",
  "title":            "Checkout - Example Store",
  "domain":           "example.com",
  "h1":               "Complete Your Purchase",
  "meta_description": "...",
  "referrer":         "https://example.com/cart",
  "active_element":   "button#submit-order",
  "timestamp_browser": 1723456789.123
}
"""

import asyncio
import csv
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class WebTracker:
    """
    Always-on WebSocket server for the companion browser extension.

    The server runs as long as the app is open.  Recording is activated /
    deactivated independently via start_recording() / stop_recording().
    """

    CSV_HEADER = [
        # ── Common ──────────────────────────────────────────────────────────────
        "timestamp_s",
        "event_index",
        # event_type values:
        #   page_visit | tab_switch | page_update | session_start
        #   element_click | form_submit | form_input_change | focus_input
        #   modal_open | modal_close | dropdown_open | dropdown_close
        #   tooltip_show | accordion_expand | accordion_collapse
        #   notification_show | error_show | alert_show | success_show
        #   loading_start | loading_end | dom_change
        #   scroll_milestone
        "event_type",
        "url",
        "domain",
        # ── Page visit fields ────────────────────────────────────────────────────
        "title",
        "h1",
        "meta_description",
        "referrer",
        # ── Element identification (shared by click, form, focus, dom) ───────────
        "element_description",   # human-readable: "Button: 'Add to Cart'"
        "element_category",      # button|link|tab|menu|input|select|checkbox|...
        "element_tag",
        "element_type",
        "element_id",
        "element_classes",
        "element_name",
        "element_href",
        "element_role",
        "element_aria_label",
        "element_placeholder",
        "element_data_testid",
        "element_text",
        "css_path",
        "page_title",
        # ── Click-specific ───────────────────────────────────────────────────────
        "element_checked",
        "element_selected_option",
        "click_x",
        "click_y",
        "page_x",
        "page_y",
        "viewport_width",
        "viewport_height",
        # ── Form-specific ────────────────────────────────────────────────────────
        "form_action",
        "form_method",
        "form_field_count",
        "form_fields_json",      # JSON of {field_name: value} (passwords redacted)
        "element_new_value",
        # ── DOM mutation sub-type ────────────────────────────────────────────────
        "dom_subtype",
        # ── Scroll-specific ──────────────────────────────────────────────────────
        "scroll_percent",
        "scroll_px",
        "page_height_px",
        # ── Raw browser timestamp ─────────────────────────────────────────────────
        "timestamp_browser",
    ]

    def __init__(
        self,
        port: int = 9877,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.port = port
        self.logger = logger or logging.getLogger(__name__)

        # Server state
        self._server_thread: Optional[threading.Thread] = None
        self._server_stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connected_clients: Set = set()
        self._server_running = False
        self._server_ready_event = threading.Event()
        self._server_start_error: Optional[Exception] = None

        # Recording state (only active during a session)
        self._recording = False
        self._output_path: Optional[Path] = None
        self.t0: Optional[float] = None

        self._lock = threading.Lock()
        self._event_index: int = 0
        self._visits: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Server lifecycle (call once — independent of sessions)
    # ------------------------------------------------------------------ #

    def start_server(self) -> None:
        """Start the always-on WebSocket server in a background daemon thread."""
        if self._server_running:
            return
        if self._server_thread is not None and self._server_thread.is_alive():
            raise RuntimeError("The previous WebSocket server thread is still shutting down.")
        self._server_thread = None
        self._server_stop_event.clear()
        self._server_ready_event.clear()
        self._server_start_error = None
        self._server_thread = threading.Thread(
            target=self._run_server, daemon=True, name="WebTracker-WS"
        )
        self._server_thread.start()
        if not self._server_ready_event.wait(timeout=5.0):
            self._server_stop_event.set()
            self._server_thread.join(timeout=2.0)
            self._server_running = False
            if not self._server_thread.is_alive():
                self._server_thread = None
            raise RuntimeError(f"WebSocket server on port {self.port} did not become ready within 5 seconds.")
        if self._server_start_error is not None:
            self._server_thread.join(timeout=1.0)
            if not self._server_thread.is_alive():
                self._server_thread = None
            raise RuntimeError(f"Could not start the WebSocket server on port {self.port}: {self._server_start_error}") from self._server_start_error
        self.logger.info("WebTracker server ready on ws://127.0.0.1:%d", self.port)

    def stop_server(self) -> None:
        """Shut down the WebSocket server cleanly (call when the app closes)."""
        if not self._server_running and self._server_thread is None:
            return
        self._server_stop_event.set()   # signals _serve() while-loop to exit
        # DO NOT call loop.stop() — that kills the loop mid-coroutine and causes
        # 'Event loop stopped before Future completed' errors.
        # Instead let _serve() exit naturally after the event is set.
        if self._server_thread is not None:
            self._server_thread.join(timeout=5.0)
            if self._server_thread.is_alive():
                raise RuntimeError("WebSocket server did not stop within 5 seconds.")
        self._server_running = False
        self._server_thread = None
        self.logger.info("WebTracker server stopped.")

    @property
    def is_server_running(self) -> bool:
        return self._server_running

    @property
    def connected_client_count(self) -> int:
        return len(self._connected_clients)

    # ------------------------------------------------------------------ #
    # Recording lifecycle (call per session)
    # ------------------------------------------------------------------ #

    def start_recording(self, t0: float, output_path: str) -> None:
        """
        Activate event recording.  Call this at session start.
        The server must already be running (start_server called).
        """
        if not self._server_running:
            raise RuntimeError("The WebSocket server must be ready before browser tracking can start.")
        self.t0 = t0
        self._output_path = Path(output_path)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._event_index = 0
        self._visits = []
        self._write_header()
        self._recording = True
        self.logger.info(
            "WebTracker recording started -> %s", self._output_path.name
        )
        # Notify connected extension(s)
        self._broadcast_cmd({"cmd": "start_recording"})

    def stop_recording(self) -> Dict[str, Any]:
        """
        Deactivate event recording.  Call this at session stop.
        The server keeps running and the extension stays connected.
        """
        self._recording = False
        self.logger.info(
            "WebTracker recording stopped (events=%d)", self._event_index
        )
        self._broadcast_cmd({"cmd": "stop_recording"})
        return self.get_summary()

    def get_summary(self) -> Dict[str, Any]:
        unique_urls: Set[str] = set()
        unique_domains: Set[str] = set()
        page_visits = []
        element_clicks = []
        for v in self._visits:
            if v.get("url"):
                unique_urls.add(v["url"])
            if v.get("domain"):
                unique_domains.add(v["domain"])
            if v.get("event_type") == "element_click":
                element_clicks.append(v)
            else:
                page_visits.append(v)

        # Click category breakdown
        from collections import Counter
        click_categories = Counter(
            c.get("element_category", "other") for c in element_clicks
        )

        return {
            "total_events": self._event_index,
            "total_page_visits": len(page_visits),
            "total_element_clicks": len(element_clicks),
            "click_categories": dict(click_categories),
            "unique_urls_visited": len(unique_urls),
            "unique_domains_visited": len(unique_domains),
            "unique_domain_names": sorted(unique_domains),
            "visits": self._visits,
        }

    # ------------------------------------------------------------------ #
    # Compat shims (match old InputLogger / WindowTracker API)
    # ------------------------------------------------------------------ #

    def start(self, t0: float) -> None:
        """Compat: start server + recording in one call (old API)."""
        self.start_server()
        # output_path must have been set externally; if not, do nothing
        if self._output_path is not None:
            self.start_recording(t0, str(self._output_path))
        else:
            self.t0 = t0

    def stop(self) -> Dict[str, Any]:
        """Compat: stop recording (server stays running)."""
        if self._recording:
            return self.stop_recording()
        return self.get_summary()

    # ------------------------------------------------------------------ #
    # Internal — asyncio WebSocket server
    # ------------------------------------------------------------------ #

    def _run_server(self) -> None:
        try:
            import websockets  # type: ignore
        except ImportError:
            self._server_start_error = RuntimeError("The 'websockets' package is not installed.")
            self.logger.error("WebTracker requires the 'websockets' package.")
            self._server_running = False
            self._server_ready_event.set()
            return

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve(websockets))
        except Exception as exc:
            self.logger.warning("WebTracker: server loop exited with: %s", exc)
            if not self._server_ready_event.is_set():
                self._server_start_error = exc
                self._server_ready_event.set()
        finally:
            # Cancel any tasks that are still pending (e.g. keepalive coroutines)
            try:
                pending = asyncio.all_tasks(self._loop)
                if pending:
                    for task in pending:
                        task.cancel()
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass
            self._server_running = False
            self._loop = None

    async def _serve(self, websockets_module) -> None:
        try:
            async with websockets_module.serve(
                self._handle_connection,
                "127.0.0.1",
                self.port,
                ping_interval=20,
                ping_timeout=10,
                reuse_address=True,   # SO_REUSEADDR: allow port reuse on restart
            ):
                self._server_running = True
                self._server_ready_event.set()
                self.logger.info("WebTracker WS server listening on ws://127.0.0.1:%d", self.port)
                # Wait until stop_server() signals us
                while not self._server_stop_event.is_set():
                    await asyncio.sleep(0.1)
                # Graceful shutdown: close all active connections first
                clients = list(self._connected_clients)
                if clients:
                    self.logger.info("WebTracker: closing %d active connection(s)…", len(clients))
                    await asyncio.gather(
                        *[ws.close() for ws in clients],
                        return_exceptions=True,
                    )
                self._connected_clients.clear()
                # async with block exits → server.close() is called cleanly
        except OSError as exc:
            self._server_start_error = exc
            self._server_ready_event.set()
            self.logger.error(
                "WebTracker: Cannot bind to port %d — %s. "
                "Kill any process using this port, or change the port in Settings.",
                self.port, exc,
            )
            self._server_running = False

    async def _handle_connection(self, websocket) -> None:
        self._connected_clients.add(websocket)
        remote = getattr(websocket, "remote_address", ("?", 0))
        self.logger.info(
            "WebTracker: tab connected from %s:%s  (total=%d)",
            *remote, len(self._connected_clients),
        )

        # Immediately tell the new tab whether recording is active
        try:
            cmd = "start_recording" if self._recording else "idle"
            await websocket.send(json.dumps({"cmd": cmd}))
        except Exception:
            pass

        try:
            async for raw in websocket:
                try:
                    data: Dict[str, Any] = json.loads(raw)
                    if self._recording:
                        self._process_event(data)
                        # No ack sent — content.js is fire-and-forget
                except json.JSONDecodeError:
                    pass
                except Exception as exc:
                    self.logger.warning("WebTracker: event processing error: %s", exc)
        except Exception:
            pass
        finally:
            self._connected_clients.discard(websocket)
            self.logger.info(
                "WebTracker: tab disconnected. (total=%d)",
                len(self._connected_clients),
            )

    def _broadcast_cmd(self, payload: Dict[str, Any]) -> None:
        """Send a command JSON to all connected extension clients (fire-and-forget)."""
        if not self._connected_clients or self._loop is None:
            return
        msg = json.dumps(payload)

        async def _send_all() -> None:
            for ws in list(self._connected_clients):
                try:
                    await ws.send(msg)
                except Exception:
                    pass

        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(_send_all(), self._loop)

    def _process_event(self, data: Dict[str, Any]) -> None:
        ts = self._timestamp()
        self._event_index += 1
        event_type = data.get("event", "page_visit")

        PAGE_EVENTS = {"page_visit", "tab_switch", "page_update", "session_start"}

        # ── Base fields (all event types) ─────────────────────────────────────
        record: Dict[str, Any] = {
            "timestamp_s":     round(ts, 6),
            "event_index":     self._event_index,
            "event_type":      event_type,
            "url":             data.get("url", ""),
            "domain":          data.get("domain", ""),
            "timestamp_browser": data.get("timestamp_browser", ""),
        }

        if event_type in PAGE_EVENTS:
            # ── Page navigation fields ─────────────────────────────────────────
            record.update({
                "title":            data.get("title", ""),
                "h1":               data.get("h1", ""),
                "meta_description": data.get("meta_description", ""),
                "referrer":         data.get("referrer", ""),
            })

        else:
            # ── Element identification (shared: click, form, focus, dom, scroll) ─
            record.update({
                "page_title":           data.get("page_title", ""),
                "element_description":  data.get("element_description", ""),
                "element_category":     data.get("element_category", ""),
                "element_tag":          data.get("element_tag", ""),
                "element_type":         data.get("element_type", ""),
                "element_id":           data.get("element_id", ""),
                "element_classes":      data.get("element_classes", ""),
                "element_name":         data.get("element_name", ""),
                "element_href":         data.get("element_href", ""),
                "element_role":         data.get("element_role", ""),
                "element_aria_label":   data.get("element_aria_label", ""),
                "element_placeholder":  data.get("element_placeholder", ""),
                "element_data_testid":  data.get("element_data_testid", ""),
                "element_text":         data.get("element_text", ""),
                "css_path":             data.get("css_path", ""),
            })

            if event_type == "element_click":
                record.update({
                    "element_checked":         str(data.get("element_checked", "")),
                    "element_selected_option": data.get("element_selected_option", ""),
                    "click_x":        data.get("click_x", ""),
                    "click_y":        data.get("click_y", ""),
                    "page_x":         data.get("page_x", ""),
                    "page_y":         data.get("page_y", ""),
                    "viewport_width":  data.get("viewport_width", ""),
                    "viewport_height": data.get("viewport_height", ""),
                })

            elif event_type.startswith("form_"):
                record.update({
                    "form_action":      data.get("form_action", ""),
                    "form_method":      data.get("form_method", ""),
                    "form_field_count": data.get("form_field_count", ""),
                    "form_fields_json": data.get("form_fields_json", ""),
                    "element_new_value": data.get("element_new_value", ""),
                    "element_checked":   str(data.get("element_checked", "")),
                })

            elif event_type == "scroll_milestone":
                record.update({
                    "scroll_percent": data.get("scroll_percent", ""),
                    "scroll_px":      data.get("scroll_px", ""),
                    "page_height_px": data.get("page_height_px", ""),
                })

            elif "dom_subtype" in data:
                record["dom_subtype"] = data.get("dom_subtype", "")

        self._visits.append(record)
        self._append_csv(record)

    def _timestamp(self) -> float:
        if self.t0 is None:
            return 0.0
        return time.monotonic() - self.t0

    def _write_header(self) -> None:
        if self._output_path is None:
            return
        with self._output_path.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(self.CSV_HEADER)

    def _append_csv(self, record: Dict[str, Any]) -> None:
        if self._output_path is None:
            return
        with self._lock:
            with self._output_path.open("a", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(
                    [record.get(col, "") for col in self.CSV_HEADER]
                )
