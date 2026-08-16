# Usability Testing Recorder (v1.1.2)

A hybrid usability testing recorder combining a minimal desktop control widget (Qt/PySide6) with a modern local web administration panel (Flask + Vanilla HTML5/CSS3 SPA). It synchronizes screen recording, input events (mouse/keyboard), active OS windows, browser-specific page interactions (clicks, menus, buttons via extension), and Pupil Labs eye trackers under a single monotonic clock reference.

---

## 📸 Architecture Overview

The application utilizes a distributed design for low CPU overhead:
1. **Desktop Recording Widget (Qt)**: A tiny, always-on-top window with only two controls: **Start/Stop Recording** and a **Settings Gear** button. It minimizes automatically when starting a recording.
2. **Local Settings Server (Flask)**: Runs a lightweight daemon server on `http://127.0.0.1:5000` to handle configuration, active recording status, session listings, analytics, and video processing.
3. **Web Control Panel (SPA)**: A modern dark-mode control panel with glassmorphism styling, accessible via browser. It includes:
   - **Recording**: Auto-saves participant IDs and task labels.
   - **Settings**: Adjust FPS, stop hotkeys, privacy modes, and Pupil Core ZMQ/Realtime settings.
   - **Sessions**: Browse recorded sessions, metadata, and open folders directly.
   - **Analysis**: Batch-analyze session folders to generate aggregated metrics.
   - **Video Processing**: Overlay animated clicks into screen recordings with live preview and variable frame rate (VFR) synchronization.
   - **Export**: Export consolidated data.

---

## 🌟 Key Capabilities

1. **Precision Input Logging**: Logs mouse moves, clicks, scrolls, and key presses relative to a monotonic clock reference (`t0`) into `events.csv`. Features a privacy mode that records keypress counts only.
2. **Robust Screen Recording**: Grabs high-quality desktop video using FFmpeg (`gdigrab`/`x11grab`) with instant fallbacks to PyMSS + OpenCV `VideoWriter`.
3. **In-Browser Activity Tracking**: Tracks active tabs, window changes, and inner-page interactions (clicks on buttons, links, menus, select controls) using a companion Chrome/Edge Extension via WebSockets (port 9877).
4. **Pupil Labs Eye Tracker Integration**: Seamless triggers for **Pupil Core** (via ZMQ) and newer **Pupil Labs Realtime API** (Neon/Invisible) with automatic time-offset calculations.
5. **Video click burn-in processor**: Renders click animation overlays onto `screen_recording_with_clicks.mp4` with a live visual preview in the browser. Uses millisecond video timestamps (`CAP_PROP_POS_MSEC`) to prevent lag on variable framerate videos.

---

## 📁 Session File Structure

Each session creates a dedicated folder under `sessions/{participant_id}_{timestamp}/`:

```
sessions/P001_20260816_120000/
├── events.csv                         # Mouse/keyboard input telemetry
├── screen_recording.mp4               # Raw screen capture video
├── screen_recording_with_clicks.mp4   # Final post-processed video with click circles
├── web_page_visits.csv                # Browser extension logs (visited URLs, element clicks)
├── session_metadata.json              # Monotonic t0, offsets, device serials, and stats
└── session.log                        # Internal system execution logs
```

---

## 🚀 Installation & Setup

### 1. Python Environment
Requires Python 3.9+. Install dependencies:
```bash
.venv\Scripts\activate   # If using a virtual environment
pip install -r requirements.txt
```

### 2. FFmpeg (Highly Recommended)
- **Windows**: Place `ffmpeg.exe` in the system `PATH` or create an `ffmpeg/` directory inside the project root folder containing `ffmpeg.exe`. 
- Without FFmpeg, the recorder automatically uses a Python-based OpenCV capture fallback.

### 3. Browser Extension Installation
1. Open Chrome/Edge and go to `chrome://extensions/`.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select the `browser_extension/` directory of this repository.
4. Ensure **Web Page Tracking** is enabled in the settings panel (uses WebSocket on port `9877`).

---

## 💻 Usage

Run the main application:
```bash
python main.py
```
1. Click the ⚙ (Gear) icon in the PyQt widget to open the Web Panel in your browser.
2. Configure your settings and input the **Participant ID** (required).
3. Hit **Start** in the desktop widget. The widget minimizes itself to clear the desktop screen.
4. Stop recording by pressing the global hotkey (default: `Ctrl+Alt+F9`) or using the stop button on the widget.
5. Head back to the Web Panel's **Video Processing** tab, select the session, and click **Start Processing** to generate the overlaid video with live preview.

---

## 📋 Version History (Changelog)

### v1.1.2 - Video Processing Integration (Current)
- Moved click overlay video post-processor from PySide6 Qt window to a pure Python background thread.
- Added video processing dashboard to Web Panel:
  - Visual session selector (shows sessions containing events and recordings).
  - Real-time video processing progress bar.
  - Live preview frame generation showing click circles as they are processed.
- Implemented `CAP_PROP_POS_MSEC` timing in OpenCV frame decoder to support variable frame rate (VFR) videos, removing overlay delays.
- Added startup offset heuristic calculations for older recorded sessions.
- Fixed preview image rendering bugs using Blob URLs.

### v1.1.1 - Web Panel Redesign
- Simplified desktop widget to a clean minimalist interface (Start/Stop + Gear icon).
- Introduced local Flask configuration server on port 5000.
- Implemented a unified modern single-page-application (SPA) web settings dashboard.

### v1.1.0 - Web & Browser Extension Tracking
- Added browser activity tracking via a dedicated Chrome/Edge Extension.
- Set up background WebSocket server (port 9877) to intercept active page visits.
- Expanded event capture to menus, button clicks, select dropdowns, and input interactions.

### v1.0.0 - Initial Release (Phase 1)
- core Qt desktop recorder.
- Simultaneous input logging, screen capturing, and ZMQ Pupil Core triggers.
- Monotonic clock reference (`t0`) implementation.

---

## ✉ Contact & Contributions

- **Author**: Mehran (mehran.ershadi@gmail.com)
- **GitHub**: [BridgeTheBit/Usability-Recorder](https://github.com/BridgeTheBit/Usability-Recorder)
- Contributions, bug reports, and suggestions are welcome. Feel free to open issues or submit pull requests.
