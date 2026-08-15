# Prompt for AI Coding Assistant — Usability Testing Recorder (Phase 1)

Copy everything below into your AI coding tool (Claude Code, Cursor, etc.) as the project brief.

---

## Project Overview

Build a **Python desktop application** for recording usability-testing sessions. This is
**Phase 1** of a larger tool (eventually similar in spirit to TechSmith Morae), but this phase
is intentionally minimal. Do not add features beyond what is listed below — extra scope will
be added in later phases.

The application must run on **Windows** (primary target — confirm if macOS/Linux support is
also needed) and be usable by a single researcher running a study with one participant at a
time, no live remote observer.

## Phase 1 Scope — exactly three capabilities

1. **Mouse and keyboard event logging** — capture and log all mouse and keyboard events
   during a session.
2. **Screen recording** — record the full screen (or a selected window/region) to a video
   file for the duration of the session.
3. **A single trigger command to a Pupil Labs eye tracker** — at session start, send a
   `recording_start()` command to a Pupil Labs device (Neon or Pupil Invisible) using the
   official `pupil-labs-realtime-api` Python package, and at session end send
   `recording_stop_and_save()`. This app does **not** need to receive or process any live
   gaze data in Phase 1 — it only starts/stops recording on the device itself.

No live/remote observer. No webcam recording. No audio recording. No live gaze streaming.
No replay/annotation GUI. No AOI (area of interest) analysis. All of these are explicitly
out of scope for Phase 1 and will be added later.

## Critical requirement: shared reference clock

All three data streams (mouse/keyboard events, screen recording, and the Pupil Labs
start/stop trigger) **must be timestamped against a single shared monotonic clock**
(e.g., Python's `time.monotonic()`), not wall-clock time. This is essential so that all
streams — including the Pupil Labs recording, which will be analyzed offline later — can be
synchronized in a future phase. Concretely:

- Record the monotonic timestamp at the exact moment the session starts (`t0`).
- Record the monotonic timestamp at the exact moment `recording_start()` is called on the
  Pupil Labs device, and log the estimated clock offset between the local machine and the
  device (the `pupil-labs-realtime-api` library exposes a method for estimating this offset
  — use it and log the result).
- Every mouse/keyboard event and every screen-recording frame (or at least the first frame)
  must be timestamped relative to `t0` using the same monotonic clock.

## Module 1 — Input Logger

- Use `pynput` to capture:
  - Mouse: move (throttled, not every pixel — sample at a reasonable rate, e.g. 30–60 Hz,
    configurable), click (button, press/release), and scroll events.
  - Keyboard: key press/release events. Do **not** log which specific character keys were
    pressed if it's normal text entry that could contain sensitive input — instead log key
    identity in a way that's useful for research (e.g., key name) but flag this as a
    configurable privacy option (full key logging vs. keystroke-count-only mode). Default to
    full key name logging, but make it a config flag.
- Output: a single CSV file per session with columns:
  `timestamp_s, event_type, detail`
  where `event_type` is one of `mouse_move`, `mouse_click`, `mouse_scroll`, `key_press`,
  `key_release`, and `detail` is a JSON string with the event-specific fields (x, y, button,
  key name, etc.).
- Must run in a background thread and not block or slow down the UI.

## Module 2 — Screen Recorder

- Record the full screen (default) to an `.mp4` file for the duration of the session.
- Use `ffmpeg` via `subprocess` for the actual encoding (preferred over a manual
  `mss` + OpenCV frame-writing loop, for stability over long recordings) — but if ffmpeg is
  not available on the system, fall back to `mss` + OpenCV `VideoWriter`. Detect which is
  available and use the better option automatically.
- Configurable frame rate (default 15–20 fps is fine for usability studies; make it a config
  option).
- Log the monotonic timestamp of the first captured frame so it can be aligned with `t0` and
  the other streams later.
- Recording must start and stop cleanly on command (tied to the same session start/stop
  actions as the input logger and the Pupil Labs trigger).

## Module 3 — Pupil Labs Trigger

- Use the official `pupil-labs-realtime-api` package (`pip install pupil-labs-realtime-api`).
- On session start:
  - Use `discover_one_device()` to find the device on the local network (with a reasonable
    timeout, e.g. 10 seconds; handle the case where no device is found gracefully — log a
    warning and continue the session without the eye tracker rather than crashing).
  - Call `device.recording_start()`.
  - Log the monotonic timestamp of this call, plus the estimated device/host clock offset.
- On session end:
  - Call `device.recording_stop_and_save()`.
  - Close the device connection cleanly (`device.close()`).
- This module should be isolated in its own class/file so it can be swapped out or extended
  (e.g. to add live gaze streaming) in a later phase without touching the other modules.

## Orchestration / Main App

- A `main.py` (or a simple `SessionController` class) that:
  - Defines a session as: participant ID (simple string/code entered by the researcher, not
    a real name) + optional task/condition label.
  - On "Start Session": records `t0`, starts all three modules in the correct order (start
    input logger and screen recorder first, then send the Pupil Labs trigger, logging each
    step's timestamp), shows a simple status indicator (recording in progress).
  - On "Stop Session": stops all three modules, sends the Pupil Labs stop command, finalizes
    all output files.
  - Writes a small `session_metadata.json` per session containing: participant ID, task
    label, session start/end wall-clock time (for human reference only, not used for sync),
    `t0` reference, Pupil Labs device offset estimate (if available), and the list of output
    files produced.

## UI

- Simple GUI using **PyQt5** (or PySide6 — pick one and be consistent) with:
  - A field for participant ID and task label.
  - Start / Stop buttons.
  - A status indicator showing what's currently recording (input logger / screen / eye
    tracker connected or not).
  - A settings field where the researcher can configure a global hotkey (e.g. captured via
    a "press a key combination" field, stored between runs — a simple local config file is
    fine) used to stop a session.
  - No need for anything more elaborate — no live preview, no charts, no replay in this
    phase.

### Window behavior during a session (important — minimize distraction)

The participant must not be distracted by the app's window while a session is running:

- When the researcher clicks **Start Session**, the main window must **minimize
  immediately** (do not just hide it — a taskbar-minimized window is the expected native
  behavior) after the session has actually started (all three modules confirmed running).
- While minimized/running, the app must listen **globally** (system-wide, not just when
  the window has focus) for the researcher-configured hotkey. Use a library such as
  `keyboard` or `pynput.keyboard.GlobalHotKeys` for this — it must work even though the
  app's window is minimized and some other application (the one under test) has focus.
  Note: registering a global hotkey listener alongside the mouse/keyboard *event logger*
  in the same process is fine, but keep them logically separate — the hotkey listener's
  only job is to detect the one configured stop combination and trigger
  `SessionController.stop_session()`; it must not itself be recorded as a regular
  logged keystroke event (filter it out of the events.csv log).
- When the configured hotkey is pressed:
  - Immediately call the stop sequence (stop input logger, stop screen recorder, send
    `recording_stop_and_save()` to the Pupil Labs device).
  - Restore/show the main window again (un-minimize) so the researcher can see the
    confirmation that the session ended and start a new one.
- The Start/Stop buttons in the GUI must also always work as a manual alternative to the
  hotkey (e.g. if the researcher is at their own machine rather than relying on the
  hotkey).
- Default hotkey suggestion: something unlikely to collide with normal application use,
  e.g. `Ctrl+Alt+F9` — but must be user-configurable and persisted across runs.

## Output structure

For each session, create a folder named `sessions/{participant_id}_{timestamp}/` containing:
- `events.csv` — mouse/keyboard log
- `screen_recording.mp4`
- `session_metadata.json`
- (Pupil Labs data itself is saved on the device/companion app, not on this machine —
  metadata.json should note the device serial number if available, to help match it up
  with this session later.)

## Error handling requirements

- If the Pupil Labs device is not found, the session should still proceed (log a clear
  warning) rather than failing entirely — the researcher may sometimes run a session without
  the eye tracker.
- If `ffmpeg` is not installed and the `mss`/OpenCV fallback is also unavailable, fail with a
  clear error message telling the user what to install.
- Never crash silently — all errors should be logged to a `session.log` file inside the
  session folder.

## Deliverables

- A working Python project with a clear folder structure (e.g. `input_logger.py`,
  `screen_recorder.py`, `pupil_trigger.py`, `session_controller.py`, `main.py`, `gui.py`).
- A `requirements.txt` listing all dependencies (`pynput`, `pupil-labs-realtime-api`,
  `PyQt5` or `PySide6`, `mss`, `opencv-python` as fallback, and `keyboard` if used for the
  global hotkey).
- A short `README.md` explaining how to install and run it, including the ffmpeg
  requirement.
- Basic inline comments explaining the timestamp-synchronization logic specifically, since
  that part is the most important for research validity.

## Explicitly out of scope for this phase (do not implement)

- Live remote observer / streaming to a second machine.
- Webcam or audio recording.
- Live gaze data streaming or processing from the Pupil Labs device.
- Replay GUI / video playback with overlays or markers.
- Area-of-interest (AOI) definition or fixation-AOI matching.
- Automatic usability metrics (time on task, click count, etc.) — this phase only needs to
  produce the raw logs; metrics computation will be a separate, later phase.
