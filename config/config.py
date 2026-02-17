# config/config.py
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict


@dataclass(frozen=True)
class AppConfig:
    """
    Strongly-typed, validated application configuration loaded from a JSON file.

    This object is intended to be the single source of truth for runtime settings.
    Values are validated (type + basic constraints) in `load_config()` so the rest of
    the codebase can assume correctness without repetitive checks.

    Expected JSON structure (overview):

    {
      "server": { "host": "...", "port": 8000 },
      "capture": { "backend": "WGC", "fps": 10 },
      "motion": {
        "diff_gain": 1.0,
        "no_motion_threshold": 0.02,
        "low_activity_threshold": 0.06,
        "ema_alpha": 0.2,
        "history_seconds": 10,
        "mean_full_scale": 0.5,
        "tile_full_scale": 0.8,
        "grid_rows": 3,
        "grid_cols": 3
      },
      "recording": {
        "enabled": true,
        "trigger_state": "NO_MOTION",
        "clip_seconds": 10,
        "cooldown_seconds": 30,
        "assets_dir": "./assets"
      },
      "ui": {
        "initial_region": { "x": 0, "y": 0, "width": 800, "height": 600 },
        "border_px": 2,
        "grid_line_px": 1
      }
    }
    """

    # -----------------------------
    # Server / API settings
    # -----------------------------
    server_host: str
    server_port: int

    # -----------------------------
    # Capture settings
    # -----------------------------
    capture_backend: str
    fps: float

    # -----------------------------
    # Motion analysis settings
    # -----------------------------
    diff_gain: float
    no_motion_threshold: float
    low_activity_threshold: float
    ema_alpha: float

    history_seconds: float
    mean_full_scale: float
    tile_full_scale: float

    grid_rows: int
    grid_cols: int

    # -----------------------------
    # Recording settings
    # -----------------------------
    recording_enabled: bool
    recording_trigger_state: str
    recording_clip_seconds: int
    recording_cooldown_seconds: int
    recording_assets_dir: str

    # -----------------------------
    # UI overlay settings
    # -----------------------------
    initial_region: Dict[str, int]
    border_px: int
    grid_line_px: int


def _require_obj(raw: Dict[str, Any], key: str) -> Dict[str, Any]:
    """
    Require `raw[key]` to exist and be a JSON object (dict).

    Used for top-level sections like "server", "motion", "ui", ... to ensure the
    config structure matches expectations early and with a clear error message.
    """
    v = raw.get(key)
    if not isinstance(v, dict):
        raise ValueError(f"Missing or invalid '{key}' object in config")
    return v


def _require_num(v: Any, key: str) -> float:
    """
    Require a numeric value (int/float) and normalize to float.

    Normalization to float avoids subtle type drift later (e.g. integer JSON values
    used where float math is expected).
    """
    if not isinstance(v, (int, float)):
        raise ValueError(f"Missing or invalid '{key}' (expected number)")
    return float(v)


def _require_str(v: Any, key: str) -> str:
    """
    Require a non-empty string.

    Note: this does not strip the returned value; callers may `.strip()` where
    whitespace-insensitivity is desired (e.g. backend/state codes).
    """
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"Missing or invalid '{key}' (expected non-empty string)")
    return v


def _opt_bool(v: Any, key: str, default: bool) -> bool:
    """
    Optional boolean with default.

    - If value is missing/None -> default.
    - If value is a bool -> return as-is.
    - Otherwise -> error (prevents "true"/"false" strings silently passing).
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    raise ValueError(f"Missing or invalid '{key}' (expected boolean)")


def _opt_int(v: Any, key: str, default: int) -> int:
    """
    Optional integer with default.

    Accepts ints and floats (JSON numbers) and converts to int.
    This allows configs like 10 or 10.0 without special-casing.
    """
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return int(v)
    raise ValueError(f"Missing or invalid '{key}' (expected number)")


def _opt_str(v: Any, key: str, default: str) -> str:
    """
    Optional string with default.

    - If value is missing/None -> default.
    - If present -> must be non-empty string.
    """
    if v is None:
        return default
    if isinstance(v, str) and v.strip():
        return v
    raise ValueError(f"Missing or invalid '{key}' (expected non-empty string)")


def load_config(path: str) -> AppConfig:
    """
    Load and validate config from a JSON file and return an `AppConfig`.

    Behavior and validation strategy:
    - Strict about required sections/keys ("server", "capture", "motion", "ui").
    - Tolerant about optional "recording" section (defaults applied if missing).
    - Normalizes some fields:
        - `capture.backend` and `recording.trigger_state` are uppercased and stripped.
    - Applies basic constraints:
        - grid_rows/grid_cols must be > 0
        - recording.clip_seconds must be > 0
        - recording.cooldown_seconds must be >= 0

    Raises:
        ValueError: If required keys are missing/invalid or constraints fail.
        OSError/IOError: If the file cannot be opened/read.
        json.JSONDecodeError: If the JSON is not valid.
    """
    # Read raw JSON to a dict. Type hint makes intent explicit for downstream helpers.
    with open(path, "r", encoding="utf-8") as f:
        raw: Dict[str, Any] = json.load(f)

    # Validate presence + shape of required top-level sections.
    server = _require_obj(raw, "server")
    capture = _require_obj(raw, "capture")
    motion = _require_obj(raw, "motion")
    ui = _require_obj(raw, "ui")

    # UI.initial_region is required and must be an object with numeric x/y/width/height.
    initial_region_obj = _require_obj(ui, "initial_region")

    # "recording" is optional; if missing, defaults are applied below via _opt_* helpers.
    recording = raw.get("recording")
    if recording is None:
        recording_obj: Dict[str, Any] = {}
    elif isinstance(recording, dict):
        recording_obj = recording
    else:
        raise ValueError("Missing or invalid 'recording' object in config (expected object)")

    # ---- Server ----
    server_host = _require_str(server.get("host"), "server.host")
    server_port = int(_require_num(server.get("port"), "server.port"))

    # ---- Capture ----
    # Uppercase backend to keep comparisons consistent across the codebase.
    capture_backend = _require_str(capture.get("backend"), "capture.backend").strip().upper()
    fps = _require_num(capture.get("fps"), "capture.fps")

    # ---- Motion analysis ----
    diff_gain = _require_num(motion.get("diff_gain"), "motion.diff_gain")
    no_motion_threshold = _require_num(motion.get("no_motion_threshold"), "motion.no_motion_threshold")
    low_activity_threshold = _require_num(motion.get("low_activity_threshold"), "motion.low_activity_threshold")
    ema_alpha = _require_num(motion.get("ema_alpha"), "motion.ema_alpha")

    history_seconds = _require_num(motion.get("history_seconds"), "motion.history_seconds")
    mean_full_scale = _require_num(motion.get("mean_full_scale"), "motion.mean_full_scale")
    tile_full_scale = _require_num(motion.get("tile_full_scale"), "motion.tile_full_scale")

    # Grid dimensions determine how the monitored region is subdivided for per-tile motion metrics.
    grid_cols = int(_require_num(motion.get("grid_cols"), "motion.grid_cols"))
    grid_rows = int(_require_num(motion.get("grid_rows"), "motion.grid_rows"))
    if grid_cols <= 0 or grid_rows <= 0:
        raise ValueError("motion.grid_cols and motion.grid_rows must be > 0")

    # ---- Recording ----
    # Defaults make recording "on" unless explicitly disabled.
    recording_enabled = _opt_bool(recording_obj.get("enabled"), "recording.enabled", True)

    # Trigger state is normalized to uppercase to avoid case-sensitive mismatches.
    recording_trigger_state = (
        _opt_str(recording_obj.get("trigger_state"), "recording.trigger_state", "NO_MOTION")
        .strip()
        .upper()
    )
    recording_clip_seconds = _opt_int(recording_obj.get("clip_seconds"), "recording.clip_seconds", 10)
    recording_cooldown_seconds = _opt_int(recording_obj.get("cooldown_seconds"), "recording.cooldown_seconds", 30)
    recording_assets_dir = _opt_str(recording_obj.get("assets_dir"), "recording.assets_dir", "./assets")

    # Minimal sanity checks to prevent non-sensical recording behavior.
    if recording_clip_seconds <= 0:
        raise ValueError("recording.clip_seconds must be > 0")
    if recording_cooldown_seconds < 0:
        raise ValueError("recording.cooldown_seconds must be >= 0")

    # ---- UI overlay ----
    # The initial region is kept as ints to align with pixel coordinates and Qt geometry.
    initial_region = {
        "x": int(_require_num(initial_region_obj.get("x"), "ui.initial_region.x")),
        "y": int(_require_num(initial_region_obj.get("y"), "ui.initial_region.y")),
        "width": int(_require_num(initial_region_obj.get("width"), "ui.initial_region.width")),
        "height": int(_require_num(initial_region_obj.get("height"), "ui.initial_region.height")),
    }

    # UI drawing parameters (border and grid line thickness in pixels).
    border_px = int(_require_num(ui.get("border_px"), "ui.border_px"))
    grid_line_px = int(_require_num(ui.get("grid_line_px"), "ui.grid_line_px"))

    # Construct the immutable config object. Downstream code uses this typed container.
    return AppConfig(
        server_host=server_host,
        server_port=server_port,
        capture_backend=capture_backend,
        fps=fps,
        diff_gain=diff_gain,
        no_motion_threshold=no_motion_threshold,
        low_activity_threshold=low_activity_threshold,
        ema_alpha=ema_alpha,
        history_seconds=history_seconds,
        mean_full_scale=mean_full_scale,
        tile_full_scale=tile_full_scale,
        grid_rows=grid_rows,
        grid_cols=grid_cols,
        recording_enabled=recording_enabled,
        recording_trigger_state=recording_trigger_state,
        recording_clip_seconds=recording_clip_seconds,
        recording_cooldown_seconds=recording_cooldown_seconds,
        recording_assets_dir=recording_assets_dir,
        initial_region=initial_region,
        border_px=border_px,
        grid_line_px=grid_line_px,
    )
