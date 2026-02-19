# motiondetector

A small Python application that detects motion in a user-defined on-screen region on **Microsoft Windows**.

You select the region using a transparent, always-on-top overlay window with a configurable grid. Motion metrics are exposed via a local **FastAPI** server (JSON endpoints + a lightweight dashboard).  
Optional: record short clips based on motion state.

---

## Documentation

- Architecture review: `docs/architecture.md`
- Developer guide: `docs/developer-guide.md`
- Product backlog and stories: `docs/backlog.md`, `docs/user-stories.md`

## Features

### Region selection overlay (Windows, always-on-top)
- Transparent, frameless overlay window you can move/resize.
- Grid overlay (rows × cols) aligned with analysis tiles.
- Optional tile numbers rendered on the overlay.
- Click tiles to toggle enabled/disabled (backed by the server).  

### Motion analysis
- Captures frames from the selected region (currently **MSS** backend).
- Computes:
  - `motion_mean` (overall motion score)
  - per-tile motion values (row-major list matching the configured grid)
  - a simple motion state machine: `NO_MOTION`, `LOW_ACTIVITY`, `MOTION`
- Supports an **analysis inset** to ignore borders/shadows that cause noisy diffs.

### Server API + dashboard
- FastAPI server for:
  - latest status
  - rolling history
  - tile disable mask
  - UI settings (e.g., show/hide tile numbers)
  - graceful shutdown via `/quit`
- Serves a browser UI from static assets.

### Optional recording
- Record short clips when a configured trigger state is active (default trigger: `NO_MOTION`).
- Cooldown + stop-grace logic to prevent clip spam / flapping.

---

## How it works

### Runtime architecture (quick view)
- **Main thread**: Qt overlay window and interaction.
- **Monitor thread**: capture + analysis loop publishing payloads.
- **Server thread**: FastAPI endpoints + dashboard assets.
- **Shared boundary**: `StatusStore` is the synchronized source of truth.

1) **DPI awareness (Windows)**  
The app sets process DPI awareness so the region you see matches physical pixels used by screen capture.

2) **Overlay UI (selection + grid)**  
The overlay is a Qt/PySide6 window. It draws the border/grid and emits the capture region during move/resize.
Importantly, the emitted region uses **Win32 client-rect-in-screen-pixels** so it stays correct across mixed-DPI monitors.

3) **Monitor loop (capture + motion)**  
A background thread captures at configured FPS, converts frames to grayscale, diffs against the previous frame, computes per-tile values + overall mean, applies smoothing/normalization, and assigns a motion state.

4) **StatusStore (shared state)**  
The monitor thread pushes payloads to a thread-safe store that also holds:
- rolling history
- disabled tile mask
- UI settings (e.g. show tile numbers)
- quit flag

5) **FastAPI server**  
Routes read/write server-side state and serve the UI.

---

## Requirements

- Windows (intended target)
- Python 3.x
- Dependencies are pinned in `requirements.lock.txt` (PySide6, mss, numpy, fastapi, uvicorn, opencv-python, ...)

---

## Install

1) Clone the repo
2) Create/activate a venv
3) Install requirements
   - Use the pinned lock file:
     - `pip install -r requirements.lock.txt`

---

## Run

Start the app:

- `python main.py`

It will:
- start the FastAPI server
- start the monitor loop thread
- show the overlay selector window

---

## Configuration (`config/config.json`)

Current config structure (high-level):

- `server.host`, `server.port`
- `capture.backend` (currently `MSS`), `capture.fps`
- `motion.*`
  - thresholds (`no_motion_threshold`, `low_activity_threshold`)
  - smoothing (`ema_alpha`)
  - normalization (`mean_full_scale`, `tile_full_scale`)
  - grid (`grid_rows`, `grid_cols`)
  - history retention (`history_seconds`)
- `recording.*` (optional)
- `ui.*` (initial region + visuals)

Tip: keep `grid_rows`/`grid_cols` reasonable; very large grids increase CPU cost and UI clutter.

### Analysis inset
If you have false positives from borders, window shadows, or the overlay chrome, configure an inset:
- `analysis_inset_px` (if present in your config version)
This inset is used consistently so overlay emission and analysis align.

---

## API

Base URL is typically:

- `http://127.0.0.1:8735`

### Main endpoints
- `GET /`  
  Dashboard HTML UI
- `GET /status`  
  Latest normalized payload
- `GET /history`  
  Rolling payload history
- `GET /tiles`  
  Returns `{ "disabled_tiles": [ ... ] }`
- `PUT /tiles`  
  Sets disabled tiles (0-based indices), e.g. `{ "disabled_tiles": [0, 5, 12] }`
- `GET /ui`  
  UI settings (server-side source of truth)
- `POST /ui/tile-numbers`  
  Toggle tile-number visibility (server-driven)
- `POST /quit`  
  Request graceful shutdown

### Payload notes
- Tiles are a single ordered list (row-major).
- Disabled tiles are represented as:
  - indices in `disabled_tiles`
  - and `None` values injected into the tiles list for those indices (so consumers don’t accidentally use masked data).

---

## Usage tips

### Disabling tiles
If parts of your selected region are “noisy” (clock, animated UI, reflections), disable those tiles:
- click tiles in the overlay
- the overlay syncs with `/tiles` and the analyzer/server will treat those tiles as masked

### Mixed-DPI / multi-monitor setups
If the overlay and capture region ever drift out of alignment:
- ensure Windows scaling settings are stable
- keep the window on a single monitor while validating
- rely on `analysis_inset_px` to avoid chrome/shadow edges

---

## Troubleshooting

### App starts but UI can’t control tiles / numbers
The overlay expects a server base URL and polls `/tiles` and `/ui`. Ensure:
- server is running
- `server.host` is reachable from the UI (loopback is recommended)

### High CPU
- Lower `capture.fps`
- Use a smaller region
- Use fewer grid rows/cols

### Recording doesn’t produce clips
Check:
- `recording.enabled: true`
- `recording.assets_dir` exists or is creatable
- Trigger state (`recording.trigger_state`) matches your desired behavior

---

## Roadmap ideas (optional)
- More capture backends (DXGI Desktop Duplication)
- Persist tile mask/UI settings to disk
- Better packaging (exe) for non-dev usage
- More robust dashboard (charts, per-tile heatmap history)
