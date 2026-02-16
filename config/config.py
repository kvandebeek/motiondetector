# config/config.py
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict


@dataclass(frozen=True)
class AppConfig:
    server_host: str
    server_port: int

    capture_backend: str
    fps: float

    diff_gain: float
    no_motion_threshold: float
    low_activity_threshold: float
    ema_alpha: float

    history_seconds: float
    mean_full_scale: float
    tile_full_scale: float

    grid_rows: int
    grid_cols: int

    recording_enabled: bool
    recording_trigger_state: str
    recording_clip_seconds: int
    recording_cooldown_seconds: int
    recording_assets_dir: str

    initial_region: Dict[str, int]
    border_px: int
    grid_line_px: int


def _require_obj(raw: Dict[str, Any], key: str) -> Dict[str, Any]:
    v = raw.get(key)
    if not isinstance(v, dict):
        raise ValueError(f"Missing or invalid '{key}' object in config")
    return v


def _require_num(v: Any, key: str) -> float:
    if not isinstance(v, (int, float)):
        raise ValueError(f"Missing or invalid '{key}' (expected number)")
    return float(v)


def _require_str(v: Any, key: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"Missing or invalid '{key}' (expected non-empty string)")
    return v


def _opt_bool(v: Any, key: str, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    raise ValueError(f"Missing or invalid '{key}' (expected boolean)")


def _opt_int(v: Any, key: str, default: int) -> int:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return int(v)
    raise ValueError(f"Missing or invalid '{key}' (expected number)")


def _opt_str(v: Any, key: str, default: str) -> str:
    if v is None:
        return default
    if isinstance(v, str) and v.strip():
        return v
    raise ValueError(f"Missing or invalid '{key}' (expected non-empty string)")


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw: Dict[str, Any] = json.load(f)

    server = _require_obj(raw, "server")
    capture = _require_obj(raw, "capture")
    motion = _require_obj(raw, "motion")
    ui = _require_obj(raw, "ui")
    initial_region_obj = _require_obj(ui, "initial_region")

    recording = raw.get("recording")
    if recording is None:
        recording_obj: Dict[str, Any] = {}
    elif isinstance(recording, dict):
        recording_obj = recording
    else:
        raise ValueError("Missing or invalid 'recording' object in config (expected object)")

    server_host = _require_str(server.get("host"), "server.host")
    server_port = int(_require_num(server.get("port"), "server.port"))

    capture_backend = _require_str(capture.get("backend"), "capture.backend").strip().upper()
    fps = _require_num(capture.get("fps"), "capture.fps")

    diff_gain = _require_num(motion.get("diff_gain"), "motion.diff_gain")
    no_motion_threshold = _require_num(motion.get("no_motion_threshold"), "motion.no_motion_threshold")
    low_activity_threshold = _require_num(motion.get("low_activity_threshold"), "motion.low_activity_threshold")
    ema_alpha = _require_num(motion.get("ema_alpha"), "motion.ema_alpha")

    history_seconds = _require_num(motion.get("history_seconds"), "motion.history_seconds")
    mean_full_scale = _require_num(motion.get("mean_full_scale"), "motion.mean_full_scale")
    tile_full_scale = _require_num(motion.get("tile_full_scale"), "motion.tile_full_scale")

    grid_cols = int(_require_num(motion.get("grid_cols"), "motion.grid_cols"))
    grid_rows = int(_require_num(motion.get("grid_rows"), "motion.grid_rows"))
    if grid_cols <= 0 or grid_rows <= 0:
        raise ValueError("motion.grid_cols and motion.grid_rows must be > 0")

    recording_enabled = _opt_bool(recording_obj.get("enabled"), "recording.enabled", True)
    recording_trigger_state = _opt_str(recording_obj.get("trigger_state"), "recording.trigger_state", "NO_MOTION").strip().upper()
    recording_clip_seconds = _opt_int(recording_obj.get("clip_seconds"), "recording.clip_seconds", 10)
    recording_cooldown_seconds = _opt_int(recording_obj.get("cooldown_seconds"), "recording.cooldown_seconds", 30)
    recording_assets_dir = _opt_str(recording_obj.get("assets_dir"), "recording.assets_dir", "./assets")

    if recording_clip_seconds <= 0:
        raise ValueError("recording.clip_seconds must be > 0")
    if recording_cooldown_seconds < 0:
        raise ValueError("recording.cooldown_seconds must be >= 0")

    initial_region = {
        "x": int(_require_num(initial_region_obj.get("x"), "ui.initial_region.x")),
        "y": int(_require_num(initial_region_obj.get("y"), "ui.initial_region.y")),
        "width": int(_require_num(initial_region_obj.get("width"), "ui.initial_region.width")),
        "height": int(_require_num(initial_region_obj.get("height"), "ui.initial_region.height")),
    }

    border_px = int(_require_num(ui.get("border_px"), "ui.border_px"))
    grid_line_px = int(_require_num(ui.get("grid_line_px"), "ui.grid_line_px"))

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
