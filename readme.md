# motiondetector

A lightweight Windows-first motion detection utility that monitors a user-defined region of the screen.  
It overlays a **transparent, always-on-top** window that you can position and resize over any application. The monitored region is sampled continuously and analyzed for motion, producing a simple **JSON status stream** including a **3×3 tile grid** (tiles 1–9) with per-tile motion metrics. The number of tiles can be adjusted via the config.json (and these are only limited by the processing power of the machine this is working on).

## Features

- Transparent overlay window (place it over any area you want to monitor)
- Always-on-top overlay so it stays visible while you work
- Region is split into a **3×3 grid** (9 tiles) for localized motion reporting, but customizable
- Outputs JSON telemetry:
  - capture status (`OK` / error + reason)
  - motion state (`NO_MOTION` / `MOTION`)
  - confidence score
  - global `motion_mean`
  - per-tile values (`tile1` … `tile9`)
- Windows capture backend support (e.g. WGC / Windows Graphics Capture)

## Example JSON output

```json
{
  "timestamp": 1771244784.4481475,
  "capture": {
    "state": "OK",
    "reason": "ok",
    "backend": "WGC"
  },
  "video": {
    "state": "NO_MOTION",
    "confidence": 0.2,
    "motion_mean": 0.43431,
    "tile1": 0.2,
    "tile2": 1.0,
    "tile3": 0.0,
    "tile4": 1.1,
    "tile5": 0.1,
    "tile6": 0.2,
    "tile7": 0.0,
    "tile8": 0.3,
    "tile9": 0.0
  }
}
```

## How it works

1. You place the overlay window over the target area.
2. The app captures the pixels behind the overlay region.
3. Frame-to-frame differences are computed to estimate motion.
4. The captured region is divided into a 3×3 grid and per-tile motion metrics are calculated.
5. A JSON snapshot is emitted on an interval (stdout and/or a file depending on configuration).

## Requirements

- Windows 10/11 (Windows-first)
- Python 3.9+ recommended
- A GPU/driver setup capable of Windows Graphics Capture (if using WGC)

## Installation

### 1) Create a virtual environment

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2) Install dependencies

```powershell
python -m pip install -r requirements.txt
```

> If your environment has multiple Python installs, prefer `python -m pip ...` to avoid `pip` launcher issues.

## Usage

> The exact entrypoint may differ depending on your repo layout. Common patterns:

### Run the app

```powershell
python -m motiondetector
```

or

```powershell
python .\src\motiondetector\main.py
```

### Typical workflow

- Start the app
- Drag/resize the transparent overlay to cover the target region
- Observe JSON output (stdout or output file)
- Use the per-tile values to understand *where* motion is happening

## Configuration

Configuration options depend on your implementation, but typical settings include:

- Capture backend: `WGC` (default) or alternatives
- FPS / capture interval
- Motion threshold(s) and confidence mapping
- Output mode:
  - stdout
  - file (e.g. `motion.jsonl`)
  - both
- Overlay options:
  - initial size/position
  - show/hide grid lines
  - hotkeys for lock/unlock, pause, exit

If you use a config file, prefer a simple `config.json` or `.env`. Example (illustrative):

```json
{
  "backend": "WGC",
  "fps": 10,
  "threshold": 0.8,
  "output": { "mode": "stdout", "path": "motion.jsonl" },
  "overlay": { "always_on_top": true, "show_grid": true }
}
```

## Tile numbering

Tiles are numbered left-to-right, top-to-bottom, example given:

```
1 2 3
4 5 6
7 8 9
```

## Troubleshooting

### `pip` fails with “Fatal error in launcher…”
Use:

```powershell
python -m pip --version
python -m pip install -r requirements.txt
```

### Capture state is not `OK`
- Ensure the target area is visible and not protected by DRM/secure surfaces
- Try running with elevated permissions if your capture method requires it
- Verify the selected backend is supported on your Windows version

### Motion seems too sensitive / not sensitive enough
- Adjust thresholds
- Reduce FPS or add smoothing (temporal averaging)
- Consider excluding UI animations or cursor if your implementation supports masks

## Roadmap (ideas)

- Optional masks / ignore zones
- Configurable grid size (e.g. 4×4)
- WebSocket or HTTP endpoint for real-time consumers
- Event hooks (e.g. run script on motion, debounce/cooldown)
- Cross-platform capture backends (later)

## License

Add your chosen license (MIT/Apache-2.0/etc.) in `LICENSE`.
