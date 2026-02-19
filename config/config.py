"""Configuration schema and JSON validation helpers.

`load_config` validates and normalizes runtime settings from `config/config.json`
into an immutable `AppConfig`, so downstream modules can assume a coherent shape and
focus on behavior instead of defensive parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict


@dataclass(frozen=True)
class AppConfig:
    """
    Strongly-typed, validated application configuration loaded from a JSON file.

    Purpose:
    - Provide a single, immutable source of truth for runtime settings.
    - Perform validation once at startup so the rest of the code can assume correctness.

    Design choices:
    - Frozen dataclass: prevents accidental mutation at runtime.
    - JSON structure grouped by functional area (server/capture/motion/recording/ui).
    - Minimal constraints here; deeper domain rules belong closer to the behavior they affect.

    Expected JSON structure (overview):

    {
      "server": { "host": "...", "port": 8000 },
      "capture": { "backend": "MSS", "fps": 10 },
      "motion": {
        "diff_gain": 1.0,
        "no_motion_threshold": 0.02,
        "low_activity_threshold": 0.06,
        "no_motion_grace_period_seconds": 1.0,
        "no_motion_grace_required_ratio": 0.8,
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
    no_motion_grace_period_seconds: float
    no_motion_grace_required_ratio: float
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
    # Audio loopback settings
    # -----------------------------
    audio_enabled: bool
    audio_device_substr: str
    audio_samplerate: int
    audio_channels: int
    audio_block_ms: int
    audio_calib_sec: float
    audio_factor: float
    audio_abs_min: float

    # -----------------------------
    # UI overlay settings
    # -----------------------------
    # Kept as a dict because it maps cleanly from JSON and is used to seed Region objects.
    initial_region: Dict[str, int]
    border_px: int
    grid_line_px: int


def _require_obj(raw: Dict[str, Any], key: str) -> Dict[str, Any]:
    """
    Require `raw[key]` to exist and be a JSON object (dict).

    Used for top-level sections like "server", "motion", "ui", ... so structural
    problems are caught early with clear, targeted errors.
    """
    v = raw.get(key)
    if not isinstance(v, dict):
        raise ValueError(f"Missing or invalid '{key}' object in config")
    return v


def _require_num(v: Any, key: str) -> float:
    """
    Require a JSON number (int/float) and normalize to float.

    Rationale:
    - JSON does not distinguish int vs float at schema level; we normalize here so
      downstream math is consistent.
    """
    if not isinstance(v, (int, float)):
        raise ValueError(f"Missing or invalid '{key}' (expected number)")
    return float(v)


def _require_str(v: Any, key: str) -> str:
    """
    Require a non-empty string.

    - Returns the original string (not stripped) so callers can decide whether whitespace
      is meaningful. Most config values should `.strip()` at assignment.
    """
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"Missing or invalid '{key}' (expected non-empty string)")
    return v


def _opt_bool(v: Any, key: str, default: bool) -> bool:
    """
    Optional boolean with default.

    Rules:
    - Missing/None => default
    - bool => value
    - anything else => error

    Prevents accidental configs like "true"/"false" (strings) from silently passing.
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    raise ValueError(f"Missing or invalid '{key}' (expected boolean)")


def _opt_int(v: Any, key: str, default: int) -> int:
    """
    Optional integer with default.

    Accepts:
    - int or float (JSON number) and converts to int.

    Note:
    - This is tolerant for JSON values like 10 or 10.0.
    - If fractional floats are provided (e.g. 10.7), they will be truncated by int().
      If that is undesirable, tighten validation here.
    """
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return int(v)
    raise ValueError(f"Missing or invalid '{key}' (expected number)")


def _opt_str(v: Any, key: str, default: str) -> str:
    """
    Optional string with default.

    Rules:
    - Missing/None => default
    - Present => must be a non-empty string
    """
    if v is None:
        return default
    if isinstance(v, str) and v.strip():
        return v
    raise ValueError(f"Missing or invalid '{key}' (expected non-empty string)")


def load_config(path: str) -> AppConfig:
    """
    Load and validate config from a JSON file and return an `AppConfig`.

    Validation strategy:
    - Strict about required sections/keys: "server", "capture", "motion", "ui".
    - Tolerant about "recording": optional section with defaults if missing.
    - Normalizes:
        - capture.backend is stripped + uppercased (consistent comparisons)
        - recording.trigger_state is stripped + uppercased
    - Applies basic constraints:
        - grid_rows/grid_cols > 0
        - recording.clip_seconds > 0
        - recording.cooldown_seconds >= 0

    Raises:
        ValueError: missing keys, invalid types, or failed constraints.
        OSError/IOError: file cannot be opened/read.
        json.JSONDecodeError: invalid JSON.
    """
    # Parse JSON into a dict. Keeping the raw structure makes it easy to validate sections.
    with open(path, "r", encoding="utf-8") as f:
        raw: Dict[str, Any] = json.load(f)

    # Required top-level sections.
    server = _require_obj(raw, "server")
    capture = _require_obj(raw, "capture")
    motion = _require_obj(raw, "motion")
    ui = _require_obj(raw, "ui")

    # UI.initial_region is required and must be a dict.
    initial_region_obj = _require_obj(ui, "initial_region")

    # Optional sections: recording + audio.
    recording = raw.get("recording")
    if recording is None:
        recording_obj: Dict[str, Any] = {}
    elif isinstance(recording, dict):
        recording_obj = recording
    else:
        raise ValueError("Missing or invalid 'recording' object in config (expected object)")

    audio = raw.get("audio")
    if audio is None:
        audio_obj: Dict[str, Any] = {}
    elif isinstance(audio, dict):
        audio_obj = audio
    else:
        raise ValueError("Missing or invalid 'audio' object in config (expected object)")

    # ---- Server ----
    server_host = _require_str(server.get("host"), "server.host")
    server_port = int(_require_num(server.get("port"), "server.port"))

    # ---- Capture ----
    # Normalize backend string to avoid case/whitespace mismatches downstream.
    capture_backend = _require_str(capture.get("backend"), "capture.backend").strip().upper()
    fps = _require_num(capture.get("fps"), "capture.fps")

    # ---- Motion analysis ----
    diff_gain = _require_num(motion.get("diff_gain"), "motion.diff_gain")
    no_motion_threshold = _require_num(motion.get("no_motion_threshold"), "motion.no_motion_threshold")
    low_activity_threshold = _require_num(motion.get("low_activity_threshold"), "motion.low_activity_threshold")
    no_motion_grace_period_seconds = _require_num(
        motion.get("no_motion_grace_period_seconds", 0.0),
        "motion.no_motion_grace_period_seconds",
    )
    no_motion_grace_required_ratio = _require_num(
        motion.get("no_motion_grace_required_ratio", 1.0),
        "motion.no_motion_grace_required_ratio",
    )
    ema_alpha = _require_num(motion.get("ema_alpha"), "motion.ema_alpha")

    if no_motion_grace_period_seconds < 0.0:
        raise ValueError("motion.no_motion_grace_period_seconds must be >= 0")
    if not (0.0 <= no_motion_grace_required_ratio <= 1.0):
        raise ValueError("motion.no_motion_grace_required_ratio must be in [0, 1]")

    history_seconds = _require_num(motion.get("history_seconds"), "motion.history_seconds")
    mean_full_scale = _require_num(motion.get("mean_full_scale"), "motion.mean_full_scale")
    tile_full_scale = _require_num(motion.get("tile_full_scale"), "motion.tile_full_scale")

    # Grid dimensions determine how per-tile values are indexed and emitted to clients.
    grid_cols = int(_require_num(motion.get("grid_cols"), "motion.grid_cols"))
    grid_rows = int(_require_num(motion.get("grid_rows"), "motion.grid_rows"))
    if grid_cols <= 0 or grid_rows <= 0:
        raise ValueError("motion.grid_cols and motion.grid_rows must be > 0")

    # ---- Recording ----
    # Defaults make recording enabled unless explicitly disabled.
    recording_enabled = _opt_bool(recording_obj.get("enabled"), "recording.enabled", True)

    # Normalize trigger_state to uppercase for consistent comparisons against analyzer states.
    recording_trigger_state = (
        _opt_str(recording_obj.get("trigger_state"), "recording.trigger_state", "NO_MOTION")
        .strip()
        .upper()
    )
    recording_clip_seconds = _opt_int(recording_obj.get("clip_seconds"), "recording.clip_seconds", 10)
    recording_cooldown_seconds = _opt_int(recording_obj.get("cooldown_seconds"), "recording.cooldown_seconds", 30)
    recording_assets_dir = _opt_str(recording_obj.get("assets_dir"), "recording.assets_dir", "./assets")

    # Sanity checks to prevent configurations that would break runtime behavior.
    if recording_clip_seconds <= 0:
        raise ValueError("recording.clip_seconds must be > 0")
    if recording_cooldown_seconds < 0:
        raise ValueError("recording.cooldown_seconds must be >= 0")


    # ---- Audio loopback ----
    audio_enabled = _opt_bool(audio_obj.get("enabled"), "audio.enabled", True)
    audio_device_substr_raw = audio_obj.get("device_substr")
    if audio_device_substr_raw is None:
        audio_device_substr = ""
    elif isinstance(audio_device_substr_raw, str):
        audio_device_substr = str(audio_device_substr_raw)
    else:
        raise ValueError("Missing or invalid 'audio.device_substr' (expected string)")
    audio_samplerate = _opt_int(audio_obj.get("samplerate"), "audio.samplerate", 48_000)
    audio_channels = _opt_int(audio_obj.get("channels"), "audio.channels", 2)
    audio_block_ms = _opt_int(audio_obj.get("block_ms"), "audio.block_ms", 250)
    audio_calib_sec = _require_num(audio_obj.get("calib_sec", 2.0), "audio.calib_sec")
    audio_factor = _require_num(audio_obj.get("factor", 2.5), "audio.factor")
    audio_abs_min = _require_num(audio_obj.get("abs_min", 0.00012), "audio.abs_min")

    if audio_samplerate <= 0:
        raise ValueError("audio.samplerate must be > 0")
    if audio_channels <= 0:
        raise ValueError("audio.channels must be > 0")
    if audio_block_ms <= 0:
        raise ValueError("audio.block_ms must be > 0")
    if audio_calib_sec < 0:
        raise ValueError("audio.calib_sec must be >= 0")
    if audio_factor <= 0:
        raise ValueError("audio.factor must be > 0")
    if audio_abs_min < 0:
        raise ValueError("audio.abs_min must be >= 0")

    # ---- UI overlay ----
    # Keep ints for pixel alignment and Qt geometry.
    initial_region = {
        "x": int(_require_num(initial_region_obj.get("x"), "ui.initial_region.x")),
        "y": int(_require_num(initial_region_obj.get("y"), "ui.initial_region.y")),
        "width": int(_require_num(initial_region_obj.get("width"), "ui.initial_region.width")),
        "height": int(_require_num(initial_region_obj.get("height"), "ui.initial_region.height")),
    }

    # UI drawing parameters in pixels.
    border_px = int(_require_num(ui.get("border_px"), "ui.border_px"))
    grid_line_px = int(_require_num(ui.get("grid_line_px"), "ui.grid_line_px"))

    # Construct immutable config used throughout the application.
    return AppConfig(
        server_host=server_host,
        server_port=server_port,
        capture_backend=capture_backend,
        fps=fps,
        diff_gain=diff_gain,
        no_motion_threshold=no_motion_threshold,
        low_activity_threshold=low_activity_threshold,
        no_motion_grace_period_seconds=no_motion_grace_period_seconds,
        no_motion_grace_required_ratio=no_motion_grace_required_ratio,
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
        audio_enabled=audio_enabled,
        audio_device_substr=audio_device_substr,
        audio_samplerate=audio_samplerate,
        audio_channels=audio_channels,
        audio_block_ms=audio_block_ms,
        audio_calib_sec=audio_calib_sec,
        audio_factor=audio_factor,
        audio_abs_min=audio_abs_min,
        initial_region=initial_region,
        border_px=border_px,
        grid_line_px=grid_line_px,
    )
