# motiondetector

`motiondetector` is a Windows-focused Python application that detects on-screen motion inside a user-selected region.

It combines:
- a transparent Qt overlay for region and tile selection,
- a background capture/analysis loop,
- a local FastAPI server for JSON and dashboard access,
- optional audio-aware state classification,
- optional synthetic test-data training mode,
- optional clip recording driven by detector state.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies:
   - `pip install -r requirements.lock.txt`
3. Run:
   - `python main.py`
4. Open dashboard:
   - `http://127.0.0.1:8735`

## Runtime architecture

- **Main thread**: Qt selector overlay and user interaction.
- **Monitor thread**: screen capture, motion analysis, state assignment, optional recording.
- **Server thread**: FastAPI + static dashboard assets.
- **Shared state boundary**: `StatusStore` (thread-safe latest payload, history, tile mask, UI settings, quit flag).

## Core capabilities

### 1) Selector overlay (Windows)
- Always-on-top transparent region selector.
- Configurable grid (`rows x cols`) aligned with analyzer tile layout.
- Click-to-toggle disabled tiles; disabled tiles are masked as `null` in API payloads.
- Runtime synchronization of grid, tile labels, overlay state text, and region through `/ui` APIs.

### 2) Motion analysis
- Capture backend: `MSS`.
- Frame differencing + grayscale-based scoring.
- Per-frame instant metrics and smoothed metrics.
- Per-tile values published in row-major order.
- Configurable thresholds for `NO_MOTION`, `LOW_ACTIVITY`, `MOTION`.
- No-motion grace voting to reduce state flapping.
- Optional analysis inset (`analysis_inset_px`) to ignore noisy borders.

### 3) Audio-aware state labeling
- Optional audio signal integration.
- Supports WASAPI session metering and loopback capture backends.
- Emits combined motion/audio state families, including audio-hardware unavailable fallbacks.

### 4) API and dashboard
- JSON endpoints for latest status, history, tile mask, UI settings, and shutdown.
- Lightweight dashboard served from `server/assets`.

### 5) Synthetic test-data trainer
- Start with `--testdata`, `--testdata-fast`, or `--testdata-slow`.
- Generates deterministic scenes and logs detector behavior to `./testdata_logs`.
- Useful for threshold tuning and regression checking without live screen dependence.

### 6) Optional recording
- Trigger clips from selected detector states.
- Cooldown and stop-grace controls help prevent noisy clip churn.

## Command-line modes

- `python main.py`
- `python main.py --testdata`
- `python main.py --testdata-fast`
- `python main.py --testdata-slow`
- `python main.py --testdata --testdata-seed 1337`

Audio utility:
- `python tools/audio_device_selector.py`
- `python tools/audio_device_selector.py --select 0`

## Configuration overview

Primary configuration file: `config/config.json`

Key groups:
- `server.*` host/port.
- `capture.*` backend/fps.
- `motion.*` thresholds, smoothing, normalization, grid, history, inset, grace voting.
- `recording.*` recorder controls.
- `audio.*` audio backend and thresholds.
- `ui.*` initial region and overlay display options.

## API summary

Base URL (default): `http://127.0.0.1:8735`

- `GET /` dashboard.
- `GET /status` latest payload.
- `GET /history` rolling payload history.
- `GET /tiles` get disabled tile indices.
- `PUT /tiles` set disabled tile indices.
- `GET /ui` get UI runtime settings.
- `POST /ui/tile-numbers` set tile-label visibility.
- `POST /ui/grid` set runtime grid size.
- `POST /ui/state-overlay` set state text overlay visibility.
- `POST /ui/region` set runtime region.
- `GET /ui/settings` compatibility alias of `/ui`.
- `POST /quit` graceful shutdown request.

## Development documentation

- Architecture: `docs/architecture.md`
- Developer guide: `docs/developer-guide.md`
- User stories: `docs/user-stories.md`
- Backlog: `docs/backlog.md`

## Platform and requirements

- Intended platform: Windows.
- Python 3.x.
- Pinned dependencies in `requirements.lock.txt`.

## Notes

- Keep server bound to loopback (`127.0.0.1`) for local-only safety unless you intentionally expose it.
- Larger tile grids increase CPU cost and visual noise.
