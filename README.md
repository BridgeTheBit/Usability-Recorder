# Atlas — Usability Recorder (v1.3.4)

A hybrid usability testing recorder combining a minimal desktop control widget (Qt/PySide6) with a modern local web administration panel (Flask + Vanilla HTML5/CSS3 SPA). It synchronizes screen recording, input events (mouse/keyboard), active OS windows, browser-specific page interactions (clicks, menus, buttons via extension), and Pupil Labs eye trackers under a single monotonic clock reference.

---

## 📸 Architecture Overview

The application utilizes a distributed design for low CPU overhead:
1. **Desktop Recording Widget (Qt)**: A tiny, always-on-top window with only two controls: **Start/Stop Recording** and a **Settings Gear** button. It minimizes automatically when starting a recording.
2. **Local Settings Server (Flask)**: Runs a lightweight daemon server on `http://127.0.0.1:5000` to handle configuration, active recording status, session listings, analytics, and video processing.
3. **Control Panel (SPA)**: A dark-mode control panel displayed inside a native Atlas window with Qt WebEngine. Its local address remains available for development. It includes:
   - **Recording**: Auto-saves participant IDs and task labels.
   - **Settings**: Adjust FPS, stop hotkeys, privacy modes, and Pupil Core ZMQ/Realtime settings.
   - **Sessions**: Browse recorded sessions, metadata, and open folders directly.
   - **Analysis**: Batch-analyze session folders to generate aggregated metrics.
   - **Video Processing**: Overlay animated clicks into screen recordings with live preview and variable frame rate (VFR) synchronization.
   - **Export**: Export consolidated data.

---

## 🌟 Key Capabilities

1. **Precision Input Logging**: Logs mouse moves, clicks, scrolls, and key presses relative to a monotonic clock reference (`t0`) into `events.csv`. Features a privacy mode that records keypress counts only.
2. **Robust Screen Recording**: Captures the desktop using FFmpeg (`gdigrab`/`x11grab`) and falls back to PyMSS + OpenCV `VideoWriter` when needed.
3. **In-Browser Activity Tracking**: Tracks active tabs, window changes, and inner-page interactions (clicks on buttons, links, menus, select controls) using a companion Chrome/Edge Extension via WebSockets (port 9877).
4. **Pupil Labs Eye Tracker Integration**: Seamless triggers for **Pupil Core** (via ZMQ) and newer **Pupil Labs Realtime API** (Neon/Invisible) with automatic time-offset calculations.
5. **Video click burn-in processor**: Renders click animation overlays onto `screen_recording_with_clicks.mp4` with a live visual preview in the control panel. Uses millisecond video timestamps (`CAP_PROP_POS_MSEC`) to prevent lag on variable framerate videos.

### Recording reliability
- A session is marked as started only after the screen recorder confirms its first real frame. The measured first-frame offset and capture backend are saved in `session_metadata.json`.
- If both screen-capture backends fail, startup reports an error instead of saving a synthetic blank video.
- Partial startup failures stop any modules that already started. During shutdown, each module is stopped even if another module reports an error; issues are saved in session metadata.
- Session folder names are made unique if two sessions start during the same second.
- Browser tracking and Pupil Labs triggers start disabled. Enable them in Settings when needed. The default stop shortcut is `Ctrl+Alt+F9`; custom shortcuts must include a modifier and one key.

---

## 📁 Session File Structure

Each session creates a dedicated folder under `sessions/{participant_id}_{timestamp}/`. In the installed Windows app, sessions and settings are stored under `<Atlas install folder>\data`; source runs store them in the project folder.

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

## 🚀 Windows installation

1. Download `AtlasSetup-1.3.4-windows-x64.exe` from [GitHub Releases](https://github.com/BridgeTheBit/Usability-Recorder/releases) and run it. It installs for the current Windows user and does not require Python, command-line setup, or administrator access.
2. Launch **Atlas** from the Start menu. Settings, recordings, and exports are kept in the `data` folder beside the installed app, so choosing D: stores them on D:. Existing default session data is moved from the previous `%LOCALAPPDATA%\Atlas\sessions` location on first launch.
3. The installer places FFmpeg at `<Atlas install folder>\ffmpeg\ffmpeg.exe`. Atlas uses that executable for screen recording and click-overlay processing; no separate FFmpeg install or PATH setup is needed.

Browser tracking is optional. The installer includes the extension and a **Browser extension setup** shortcut. Chrome and Edge require the user to approve an unpacked extension once; no browser extension can be silently enabled by a normal desktop installer. The extension can be skipped when browser tracking is not needed.

### Run from source

For developers, Python 3.10+ is required:
```powershell
python -m pip install -r requirements.txt
python main.py
```
For instructions to build the Windows installer, see [packaging/README.md](packaging/README.md).

---

## 💻 Usage

### Session review

In **Session Preview**, choose a recording from the playlist beside the video. The player
provides frame steps, five-second skips, speed control, and separate click, scroll, key,
comment, and future eye-tracking lanes. Click types use distinct colors and an L/R/M
overlay on the video. Insert a comment at a frame or marked range; comments are saved in
`review_notes.json` beside the session and appear briefly on the video during playback.
The preview copy is made with bundled FFmpeg. A sidebar switch selects dark or light theme.
The sidebar also shows the installed version and checks GitHub Releases for updates.
Key names and typed text are never returned by the review API.


1. Click the ⚙ (Gear) icon in the desktop widget to open the control panel in an Atlas window.
2. Configure your settings and input the **Participant ID** (required).
3. Hit **Start** in the desktop widget. The widget minimizes itself to clear the desktop screen.
4. Stop recording by pressing the global hotkey (default: `Ctrl+Alt+F9`) or using the stop button on the widget.
5. Head back to the control panel's **Video Processing** tab, select the session, and click **Start Processing** to generate the overlaid video with live preview.

---

## 📋 Version History (Changelog)

### v1.2.7 - Recording timing and complete session review
- Task duration uses monotonic boundaries from recording readiness to the stop request,
  including idle periods and excluding initialization and shutdown. Input metrics use
  the same interval. Historical sessions are explicitly labeled as estimates in exports.
- FFmpeg alignment uses the first input frame's integer wall-clock PTS, mapped to the
  session monotonic clock, rather than asynchronous progress-message arrival time.
- OpenCV fallback creates capture resources on its worker thread and duplicates held
  frames for missed time slots instead of shortening slow recordings.
- Review loads every page of events, including sessions exceeding 2,000 interactions.
- Standalone click processing applies the recorded video offset. Pre-video events are
  excluded from overlays instead of being moved to the first frame.
- Nine regression tests cover idle tasks, stop boundaries, pagination, privacy, slow
  capture and FFmpeg timestamp parsing (including a real FFmpeg smoke test).

Timing remains limited by capture sampling and device latency. Validate synchronization
on the study workstation before collecting research data; this is not hardware-level
synchronization. Existing videos cannot have their original capture timing reconstructed.

### v1.2.6 - Refined session preview
- Add a light theme, video-side session playlist, compact event tracks, and player controls.
- Show frame comments briefly over playback and distinguish left, right, and middle clicks.
- Keep the player and timeline visible together without page scrolling.

### v1.2.5 - Timeline preview and comments
- Add a dedicated Session Preview page with event tracks and frame controls.
- Convert recordings to a WebM review copy with bundled FFmpeg for embedded playback.
- Save frame and range comments beside each session; show version and release updates.

### v1.2.4 - Initial session review
- Replay a recorded session inside the Analysis page and seek to captured interactions.
- Align review events to the measured first video frame without exposing key names.

### v1.2.3 - Native control panel and Atlas icon
- Use the supplied transparent logo in the app window, control panel, executable, shortcuts, and installer.
- Open the local control panel in a dedicated Windows window instead of the default browser.
- Hide the FFmpeg console window during screen recording and video processing.

### v1.2.2 - Install-local FFmpeg and data paths
- Bundled FFmpeg at `<install folder>\ffmpeg\ffmpeg.exe` and use that path for recording and video processing.
- Store settings, sessions, and exports under `<install folder>\data` so their drive follows the chosen install location.
- Migrate settings and the previous default sessions folder on first launch.

### v1.2.1 - Standalone video processing
- Removed the post-processing requirement for a separately installed FFmpeg executable.
- Use the PyAV media libraries bundled with Atlas to encode click overlays and preserve source audio.

### v1.2.0 - Windows installer
- Added a per-user Windows installer with the app runtime and dependencies included.
- Moved installed settings, recordings, and exports to the user's local application data folder.
- Included the optional browser extension and setup instructions with the installer.

### v1.1.2 - Video Processing Integration
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
