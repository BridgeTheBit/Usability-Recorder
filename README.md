# Usability Testing Recorder (Phase 1)

A Python desktop application for conducting usability testing sessions with single-participant research studies. Phase 1 focuses on lightweight, robust raw data recording across three synchronized streams without live streaming or replay GUIs.

---

## Key Capabilities (Phase 1 Scope)

1. **Mouse & Keyboard Event Logging**: Captures mouse moves (sampled at configurable Hz), clicks, scrolls, and key events into `events.csv`. Supports a privacy mode (keystroke count only).
2. **Full Screen Recording**: Records the primary screen to an `.mp4` video. Prefers `ffmpeg` for efficiency and stability, with fallback to `mss` + `opencv-python`.
3. **Pupil Labs Eye Tracker Trigger**: Supports **Pupil Capture (Pupil Core)** via ZMQ Remote API (`tcp://127.0.0.1:50020` or configurable Remote IP/Port) AND **Pupil Labs Realtime API** for Neon / Pupil Invisible devices.

---

## Pupil Capture (Pupil Core) Setup & Remote IP Configuration

To control recording in **Pupil Capture**:
1. Launch **Pupil Capture** software on the local host or remote recording computer.
2. In Pupil Capture, ensure the **Pupil Remote** plugin is active (default listening port is `50020`).
3. In this application's GUI:
   - Select **Pupil Capture (ZMQ)** as the Connection Mode.
   - Enter the **Remote IP** (e.g. `127.0.0.1` for local machine or `192.168.x.x` for remote).
   - Enter the **ZMQ Port** (default `50020`).
4. When you click **Start Session**, the application sends the `'R {session_name}'` command over ZMQ to trigger recording start in Pupil Capture. When you stop the session, it sends `'r'` to stop and save the recording.

---

## Critical Requirement: Shared Reference Clock (`t0`)

All data streams are timestamped against a single shared monotonic clock (`time.monotonic()`), **not wall-clock time**. 

- **Session Reference (`t0`)**: Recorded the instant "Start Session" is executed.
- **`events.csv`**: Every event timestamp column (`timestamp_s`) is `time.monotonic() - t0`.
- **Screen Recording**: First frame timestamp relative to `t0` is recorded in `session_metadata.json`.
- **Pupil Labs Clock Offset**: Estimated clock offset (via ZMQ `'t'` time query or device clock offset API) and trigger timestamps relative to `t0` are recorded for offline synchronization in future phases.

---

## Folder & File Structure

Each session creates a dedicated folder under `sessions/{participant_id}_{timestamp}/`:

```
sessions/P001_20260801_120000/
├── events.csv                 # Mouse/keyboard logs (timestamp_s, event_type, detail)
├── screen_recording.mp4       # Full screen video file
├── session_metadata.json      # Metadata, clock offsets, t0, and file paths
└── session.log                # Execution log and warnings
```

---

## Installation

### 1. Python Environment

Ensure Python 3.9+ is installed. Install requirements:

```bash
pip install -r requirements.txt
```

### 2. FFmpeg (Preferred for Video Recording)

- On **Windows**, install FFmpeg and add `ffmpeg.exe` to your system `PATH` (or place `ffmpeg.exe` inside an `ffmpeg/` folder in this project root).
- If FFmpeg is missing, the application automatically falls back to `mss` + OpenCV `VideoWriter`.

---

## Usage

Start the application GUI:

```bash
python main.py
```

### Session Controls & Hotkey Behavior

1. Enter **Participant ID** (required) and optional **Task / Condition**.
2. Configure **Pupil Labs Eye Tracker Settings** (Connection Mode, Remote IP, ZMQ Port).
3. Select desired **FPS** (default 15) and **Privacy Mode** preference.
4. Click **Start Session**:
   - The application starts all 3 recording streams.
   - The main GUI **minimizes immediately** to avoid distracting the study participant.
5. Stop the session:
   - Press the global stop hotkey (default: `Ctrl+Alt+F9`, configurable in UI).
   - Alternatively, restore the GUI from the taskbar and click **Stop Session**.
6. Upon stopping, all streams are finalized and the window restores automatically with a completion summary.
# AI Integration Overview

This repository now includes an **AI Overview** file (`ai_overview.md`) that describes the internal architecture, data flow, and synchronization mechanisms of the Usability Testing Recorder. This document is intended for future AI agents to understand the program state, metadata, and how to interact programmatically.

- **File**: `ai_overview.md` – Provides a concise, machine‑readable description of:
  - Shared reference clock (`t0`) and how timestamps are generated.
  - Interaction with Pupil Labs eye tracker (ZMQ and Realtime API).
  - Session lifecycle managed by `SessionController`.
  - Locations of generated data (`events.csv`, `screen_recording.mp4`, `session_metadata.json`).
  - Recommended entry points for AI agents (e.g., reading `session_metadata.json` for offsets, parsing `events.csv` for click/keystroke metrics).

AI systems can ingest this file to automate analysis, generate reports, or coordinate additional sensors.

---

## Future Work

- **Phase 2**: Implement live replay UI, real‑time gaze overlay, and remote data streaming.
- **Phase 3**: Add machine‑learning based interaction classification and automatic report generation.
- **Enhanced Eye‑Tracker Integration**: Support multiple simultaneous eye‑trackers and automated calibration routines.

---

## License

This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.

---

## Contact & Contributions

- **Author**: Mehran (email@example.com)
- **GitHub**: https://github.com/mehran/usability-recorder
- Contributions are welcome! Please open issues or submit pull requests following the contribution guidelines.

---

*End of README*
