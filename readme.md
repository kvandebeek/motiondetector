# motiondetector

A small Python application that detects motion in a user-defined on-screen region on **Microsoft Windows**.

You select the region using a transparent, always-on-top overlay window with a configurable grid. Motion metrics are exposed via a local **FastAPI** server (JSON endpoints + a lightweight dashboard).  
Optional: record short clips based on motion state.

It also includes a **synthetic test-data trainer mode** to exercise detection thresholds and state transitions without relying on live screen content.

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
- Optional overlay of the current detector state text (`show_overlay_state`).
- Click tiles to toggle enabled/disabled (backed by the server).
- Runtime UI settings poll/sync (`/ui`) so grid size, tile labels, and region can be updated live.

### Motion analysis
- Captures frames from the selected region (currently **MSS** backend).
- Computes:
  - `motion_mean` (overall motion score)
  - `motion_instant_mean`, `motion_instant_top1`, and top-k activity proxy (`motion_instant_activity`)
  - confidence score (`video.confidence`)
  - per-tile motion values (row-major list matching the configured grid)
  - motion+audio states (`NO_MOTION_WITH_AUDIO`, `LOW_ACTIVITY_NO_AUDIO`, `MOTION_WITH_AUDIO`, etc.) and fallback `*_NOSOUNDHARDWARE` when audio hardware/session metering is unavailable
- Includes no-motion grace voting (`no_motion_grace_period_seconds` + `no_motion_grace_required_ratio`) to reduce state flapping.
- Supports tile masking from the UI (`disabled_tiles`) and publishes masked tiles as `null`.
- Emits explicit `ALL_TILES_DISABLED` state when every tile is masked.
- Supports an **analysis inset** to ignore borders/shadows that cause noisy diffs.

### Server API + dashboard
- FastAPI server for:
  - latest status
  - rolling history
  - tile disable mask
  - UI settings (tile numbers, overlay state, grid shape, region)
  - graceful shutdown via `/quit`
- Serves a browser UI from static assets.

### Synthetic test-data trainer (optional)
- Launch with `--testdata`, `--testdata-fast`, or `--testdata-slow`.
- Opens a generated-content window coupled to the selector window geometry.
- Polls `/status` and logs detector results for scene-by-scene evaluation.
- Writes run logs and summary artifacts into `./testdata_logs`.

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
- Dependencies are pinned in `requirements.lock.txt` (PySide6, mss, numpy, fastapi, uvicorn, opencv-python, pyaudiowpatch, ...)

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

Optional trainer modes:

- `python main.py --testdata`
- `python main.py --testdata-fast`
- `python main.py --testdata-slow`
- `python main.py --testdata --testdata-seed 1337`

Audio device discovery/selection utility (stand-alone):

- `python tools/audio_device_selector.py` (interactive selection)
- `python tools/audio_device_selector.py --select 0` (non-interactive)

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
  - grace voting (`no_motion_grace_period_seconds`, `no_motion_grace_required_ratio`)
  - smoothing (`ema_alpha`)
  - normalization (`mean_full_scale`, `tile_full_scale`)
  - grid (`grid_rows`, `grid_cols`)
  - history retention (`history_seconds`)
- `recording.*` (optional)
  - `stop_grace_seconds` supported (if present)
- `blockiness.*` (optional, default enabled)
  - `block_sizes` (default `[8, 16]`)
  - `sample_every_frames` (default `25`)
  - `downscale_width` (default `640`)
  - `ema_alpha` (default `0.25`)
- `audio.*` (optional loopback meter settings)
  - `enabled` (default `true`)
  - `backend` (`pycaw`/`wasapi_session` for WASAPI session metering, or `pyaudiowpatch` for loopback capture)
  - `device_id` (stable identifier written by `tools/audio_device_selector.py`)
  - `device_index` (`-1` for auto-select, or a concrete loopback input index)
  - `device_substr` (optional substring match for auto-select)
  - `samplerate`, `channels`, `block_ms`
  - `process_names` (comma-separated optional process filter, e.g. `chrome.exe,msedge.exe`)
  - `on_threshold`, `off_threshold`, `hold_ms`, `smooth_samples` for audio-present hysteresis
  - if `audio.device_id` is configured but not found at runtime, the app warns and falls back to auto-selection
- `ui.*` (initial region + visuals + `show_tile_numbers`)
  - includes `show_overlay_state` when enabled

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
- `POST /ui/grid`
  Update runtime grid rows/cols
- `POST /ui/state-overlay`
  Toggle selector overlay state label rendering
- `POST /ui/region`
  Update runtime region (x/y/width/height)
- `GET /ui/settings`
  Compatibility alias of `/ui`
- `POST /quit`  
  Request graceful shutdown

### Payload notes
- `audio` is included with `available`, `left`, `right`, `detected`, and `reason` (`left/right` are 0..100).
- Status now includes video confidence and instant metrics (`motion_instant_mean`, `motion_instant_top1`, `motion_instant_activity`).
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

### Audio capture/session metering unavailable
If `/status` returns `"audio.available": false` with `capture_failed:*`:
- install pinned dependencies again (`pip install -r requirements.lock.txt`)
- for WASAPI session metering, set `audio.backend` to `pycaw` and optionally scope `audio.process_names`
- for loopback fallback, set `audio.backend` to `pyaudiowpatch` and pin `audio.device_index`
- validate loopback hardware path with:
  - `python monitor_audio_output_loopback.py --device-index 13`

Press `Ctrl+C` in that helper script (or in `python main.py`) to stop gracefully.
Note: testing showed that a WASAPI device like "id=loopback::windows-wasapi::speakers-realtek-r-audio-loopback | name=Speakers (Realtek(R) Audio) [Loopback] | host_api=Windows WASAPI | in=2 | out=0 [loopback-like]" functions fine in most cases.

### High CPU
- Lower `capture.fps`
- Use a smaller region
- Use fewer grid rows/cols

### Recording doesn’t produce clips
Check:
- `recording.enabled: true`
- `recording.assets_dir` exists or is creatable
- Trigger state (`recording.trigger_state`) matches your desired behavior

### Testing/tuning with synthetic scenes
- Run one of the `--testdata*` modes.
- Compare `expected_state` (generated scenes) against actual `/status.video.state` in logs.
- Review generated summaries in `./testdata_logs` after the run.

---

## Roadmap ideas (optional)
- More capture backends (DXGI Desktop Duplication)
- Persist tile mask/UI settings to disk
- Better packaging (exe) for non-dev usage
- More robust dashboard (charts, per-tile heatmap history)
