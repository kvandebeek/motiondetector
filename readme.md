# motiondetector

A small Python application that detects motion in a user-defined on-screen region on **Microsoft Windows**. The region is selected using a transparent, always-on-top overlay window. Motion metrics are exposed through a local **FastAPI** server (JSON + a simple dashboard page). ([github.com](https://github.com/kvandebeek/motiondetector))

---

## What it does

- Shows a **transparent overlay window** you can move/resize over any other window (always on top).
- Captures frames from that region (currently via **MSS**) and computes motion.
- Splits the region into a configurable grid (rows * columns, in config.json) and produces:
  - overall motion score (`motion_mean`)
  - per-tile motion values (`tiles` / `tiles_named`)
  - a simple state machine: `NO_MOTION`, `LOW_ACTIVITY`, `MOTION`
- Serves the latest status + a rolling history through an API, plus a lightweight dashboard page.
- Optionally records short video clips when a configured trigger state is active (defaults to `NO_MOTION`). ([github.com](https://github.com/kvandebeek/motiondetector/raw/main/main.py))

---

## Repository layout

```
.
├─ main.py                       # App entry point: loads config, starts server + monitor loop
├─ readme.md                     # Project documentation (setup, usage, configuration, troubleshooting)
├─ docs/                         # Additional documentation (design notes, screenshots, deep-dives, etc.)
├─ requirements.lock.txt         # Pinned Python dependencies for reproducible installs
├─ analyzer/
│  ├─ capture.py                 # Screen capture backends + Region model (grabs frames from the selected area)
│  ├─ monitor_loop.py            # Main detection loop: compute motion metrics per tile + overall state; drives recorder + status updates
│  ├─ monitor_windows.py         # Windows-specific wiring/helpers for running the monitor on Windows
│  └─ recorder.py                # Clip recording logic (triggered by state changes / cooldowns)
├─ config/
│  ├─ config.json                # User-editable settings (fps, thresholds, grid size, region defaults, UI styling)
│  └─ config.py                  # Config schema + validation/parsing utilities
├─ server/
│  ├─ server.py                  # FastAPI server exposing status endpoints + serving the UI/HTML
│  ├─ status_store.py            # In-memory status store shared between monitor loop and API
│  └─ server_html_contents.py    # Embedded/served HTML content for the status page(s)
└─ ui/
   └─ selector_ui.py             # Transparent always-on-top region selector window with grid overlay (PySide6)

```

(Reflects the modules imported/used by the entrypoint.) ([github.com](https://github.com/kvandebeek/motiondetector/raw/main/main.py))

---

## How it works (high-level)

1. **Windows DPI awareness**
   - The app sets process DPI awareness so screen coordinates match physical pixels when Windows scaling is enabled. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/analyzer/monitor_windows.py))

2. **Overlay UI (region selection)**
   - A frameless, translucent PySide6 window is created.
   - It is always-on-top and can be moved/resized by dragging edges/corners.
   - It draws a cyan border and a **3×3** (depending on configuration in config.json) guide grid on the overlay. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/ui/selector_ui.py))

3. **Capture + motion analysis**
   - The monitor loop runs in a background thread at the configured FPS.
   - Frames are captured from the selected region (BGRA) and converted to grayscale.
   - The per-frame absolute difference vs the previous frame is computed and summarized:
     - `mean_raw` (global average difference)
     - `tiles_raw` (per-tile mean difference)
   - Values are normalized (`*_full_scale`) and smoothed using an EMA (`ema_alpha`).
   - The app assigns a state: `NO_MOTION` / `LOW_ACTIVITY` / `MOTION`. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/analyzer/capture.py))

4. **Status storage + API**
   - Latest payload + rolling history are kept in-memory (`StatusStore`) for `history_seconds`.
   - FastAPI serves:
     - `/status` (latest)
     - `/history` (list of past payloads)
     - `/quit` (request shutdown)
     - `/` (simple dashboard HTML) ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/server/status_store.py))

5. **Optional recording**
   - If enabled, a `ClipRecorder` can write MP4 (fallback AVI) clips into `assets_dir`.
   - Start/stop behavior is controlled by `trigger_state`, `clip_seconds`, `cooldown_seconds`, and `stop_grace_seconds`. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/analyzer/recorder.py))

---

## Requirements

- Windows (intended target)
- Python (your environment)
- Key dependencies include:
  - PySide6 (overlay UI)
  - mss + numpy (capture + processing)
  - fastapi + uvicorn (API server)
  - opencv-python (recording)
- A pinned dependency set exists in `requirements.lock.txt`. ([github.com](https://github.com/kvandebeek/motiondetector/raw/main/requirements.lock.txt))

---

## Install

Clone this repository
Install the necessary requirements
Launch via python main.py (or in your own virtual environment)

```

> If you prefer a non-locked requirements file, you can generate one from the lock, but the repository currently provides `requirements.lock.txt` as the canonical list. ([github.com](https://github.com/kvandebeek/motiondetector/raw/main/requirements.lock.txt))

---

## Configure

Edit `config/config.json`. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/config/config.json))

### Current default config (summary)

- Server: `127.0.0.1:8735`
- Capture: backend `MSS`, `fps: 5`
- Motion:
  - `diff_gain`, thresholds, EMA alpha
  - grid size: `grid_rows`, `grid_cols` (defaults to 25×25)
  - history retention: `history_seconds` (defaults to 120s)
  - normalization targets: `mean_full_scale`, `tile_full_scale`
- Recording (optional): enabled + clip/cooldown + `assets_dir`
- UI: initial region and overlay line widths ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/config/config.json))

---

## Run

From repository root:

```powershell
python .\main.py
```

This will:
- start the API server in a background thread
- start the monitor loop in a background thread
- show the overlay selector window (drag to move/resize)
- continuously publish status/history until you close the overlay or call `/quit` ([github.com](https://github.com/kvandebeek/motiondetector/raw/main/main.py))

---

## API

Base URL: `http://<host>:<port>` from `config.json`. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/config/config.json))

### `GET /`
Returns a small HTML dashboard page. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/server/server.py))

### `GET /status`
Returns the latest status payload. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/server/server.py))

### `GET /history`
Returns `{"history": [...]}` where the list contains past status payloads within `history_seconds`. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/server/server.py))

### `POST /quit`
Requests the app to quit (main loop polls the quit flag). ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/server/server.py))

---

## Status JSON format

A typical payload produced by the monitor loop looks like:

- Top-level:
  - `timestamp`
  - `capture` (state, reason, backend)
  - `video` (motion state, confidence, motion_mean, grid, tiles, tiles_named, etc.)
  - `overall` (OK / NOT_OK)
  - `errors`
  - `region` (x, y, width, height) ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/analyzer/monitor_loop.py))

Notes:
- `video.tiles` is a list of normalized floats (0 → 1).
- `video.tiles_named` maps `t1..tN` to those values, where `N = grid_rows * grid_cols`. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/analyzer/monitor_loop.py))
- The default/fallback payload in `StatusStore` uses `tile1..tile9`; once the monitor loop runs, the newer `tiles/tiles_named` structure is used. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/server/status_store.py))

---

## Recording behavior

Recording is implemented in `analyzer/recorder.py` and driven from the monitor loop. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/analyzer/recorder.py))

Configuration keys:

```json
"recording": {
  "enabled": true,
  "trigger_state": "NO_MOTION",
  "clip_seconds": 10,
  "cooldown_seconds": 30,
  "assets_dir": "./assets"
}
```

- When `enabled` and `state == trigger_state`, a clip is started.
- MP4 is attempted first; AVI is used as fallback if MP4 writer cannot be opened.
- Clips are written into `assets_dir`. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/analyzer/recorder.py))

---

## Known implementation details

- **Capture backend**: `ScreenCapturer` currently supports only `MSS` and uses thread-local MSS resources (to avoid Windows MSS thread-local handle issues). ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/analyzer/capture.py))
- **Overlay grid vs analysis grid**:
  - The overlay currently draws a fixed **3×3** guide grid.
  - The analysis grid (`grid_rows`/`grid_cols`) is configurable and defaults to **25×25**.
  - These are independent in the current implementation. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/ui/selector_ui.py))

---

## Development tips

- If you change the API port/host, update `config/config.json`. ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/config/config.json))
- If motion values look “too small” or “too noisy”, tune:
  - `diff_gain`
  - `mean_full_scale` / `tile_full_scale`
  - `no_motion_threshold` / `low_activity_threshold`
  - `ema_alpha` ([raw.githubusercontent.com](https://raw.githubusercontent.com/kvandebeek/motiondetector/main/config/config.json))

---

## License

No license file is currently included in the repository. Add one (MIT/Apache-2.0/etc.) if you plan to distribute this publicly.
