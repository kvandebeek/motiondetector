# File commentary: analyzer/payload_normalize.py - This file holds logic used by the motion detector project.
from __future__ import annotations

"""
payload_normalize.py

Builds the normalized JSON payload emitted by the monitor/analyzer.

This module centralizes the wire-format so other parts of the app (server/UI/clients)
can rely on a stable schema.

Schema goals:
- No duplicated representations of the same data (no tile1..tileN + tiles + tiles_named).
- Tiles are a single ordered list; disabled tiles are represented as None + an index list.
- `tiles_indexed` provides explicit tile numbers plus value for each tile.
- Keep "debug" out of the default payload (gate it elsewhere if needed).
- Keep timestamp sources consistent.
"""

from dataclasses import dataclass
from typing import Any, Optional, Sequence
import math
import time


@dataclass(frozen=True)
class RegionPayload:
    """
    JSON-serializable description of the monitored region.

    Notes:
    - `monitor_id` is intended to identify the source monitor (if known) in a way that
      is consistent with the capture backend (e.g., MSS monitor indices).
    - x/y/width/height are expressed in the same coordinate space as capture (typically
      virtual desktop coordinates for MSS).
    """
    monitor_id: int
    x: int
    y: int
    width: int
    height: int


def _finite_or_none(v: object) -> float | None:
    """
    Convert an arbitrary tile value into a JSON-safe float or None.

    Conventions:
    - None => None (used to represent a disabled/masked tile).
    - NaN/Inf => None (invalid measurement; JSON consumers should treat as missing).
    - bool => None (avoid `True/False` silently becoming 1.0/0.0).
    - int/float => float(v) if finite.
    - anything else => None.

    Rationale:
    - JSON has no NaN/Inf, and different serializers handle them differently.
      Normalizing here avoids “sometimes invalid JSON” bugs downstream.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        fv = float(v)
        return None if (math.isnan(fv) or math.isinf(fv)) else fv
    return None


def build_payload(
    *,
    capture_state: str,
    capture_reason: str,
    backend: str,
    video_state: str,
    confidence: float,
    motion_mean: float,
    tiles: Sequence[object],
    grid_rows: int,
    grid_cols: int,
    stale: bool,
    stale_age_sec: float,
    region: RegionPayload,
    overall_state: str,
    overall_reasons: Sequence[str],
    errors: Optional[Sequence[str]] = None,
    ts: Optional[float] = None,
) -> dict[str, Any]:
    """
    Create the normalized payload expected by downstream consumers.

    Key guarantees:
    - Stable schema (keys and nesting remain consistent across the app).
    - `tiles` is always a list of length rows*cols, row-major.
    - `disabled_tiles` is derived from tiles where value is None.
    - `tiles_indexed` mirrors `tiles` with explicit tile indices and disabled markers.
    - Timestamp is always epoch seconds as float.

    Args:
        capture_state: Capture subsystem state (e.g., "OK", "ERROR").
        capture_reason: Human-readable reason for capture_state.
        backend: Capture backend identifier (e.g., "MSS").
        video_state: Motion classification state (e.g., "MOTION", "NO_MOTION").
        confidence: Classifier confidence (caller defines semantics; typically 0..1).
        motion_mean: Aggregate motion metric (caller defines semantics; typically 0..1).
        tiles: Per-tile motion metrics; must be length == grid_rows * grid_cols.
               Disabled tiles should be passed as None (NaN/Inf also become None).
        grid_rows: Tile grid rows (> 0).
        grid_cols: Tile grid cols (> 0).
        stale: Whether the payload is considered stale (consumer should treat as not current).
        stale_age_sec: How long (seconds) the payload has been stale.
        region: Region geometry for the monitored area.
        overall_state: High-level state (e.g., "OK" / "NOT_OK").
        overall_reasons: List of reason strings (stable identifiers).
        errors: Optional list of error strings.
        ts: Optional timestamp override (epoch seconds). If omitted, uses time.time().

    Returns:
        A dict ready for JSON serialization.
    """
    # Single timestamp source for the entire payload.
    # Allow override for deterministic tests or when a timestamp is computed upstream.
    now_ts = float(time.time() if ts is None else ts)

    # Validate grid dimensions early to avoid producing malformed payloads.
    rows = int(grid_rows)
    cols = int(grid_cols)
    if rows <= 0 or cols <= 0:
        raise ValueError("grid_rows and grid_cols must be positive integers")

    expected = rows * cols

    # Defensive validation: a mismatched tiles list breaks clients that assume fixed indexing.
    if len(tiles) != expected:
        raise ValueError(f"tiles length must be {expected} for grid {rows}x{cols}")

    # Normalize each tile value into a JSON-friendly representation.
    tiles_list: list[float | None] = [_finite_or_none(v) for v in tiles]

    # Disabled tiles are those explicitly None after normalization (disabled or invalid).
    # Keeping both representations is useful: tiles carries nulls; disabled_tiles makes it easy
    # for clients to style/skip tiles without scanning the entire list.
    disabled_tiles = [i for i, v in enumerate(tiles_list) if v is None]
    tiles_indexed = [
        {"tile": i, "value": "disabled" if v is None else float(v)}
        for i, v in enumerate(tiles_list)
    ]

    return {
        "timestamp": now_ts,
        "capture": {
            "state": str(capture_state),
            "reason": str(capture_reason),
            "backend": str(backend),
        },
        "video": {
            "state": str(video_state),
            "confidence": float(confidence),
            "motion_mean": float(motion_mean),
            "grid": {"rows": rows, "cols": cols},
            "tiles": tiles_list,
            "tiles_indexed": tiles_indexed,
            "disabled_tiles": disabled_tiles,
            "stale": bool(stale),
            "stale_age_sec": float(stale_age_sec),
        },
        "overall": {
            "state": str(overall_state),
            "reasons": [str(r) for r in overall_reasons],
        },
        # Normalize errors to a list for schema stability.
        "errors": list(errors) if errors is not None else [],
        "region": {
            "monitor_id": int(region.monitor_id),
            "x": int(region.x),
            "y": int(region.y),
            "width": int(region.width),
            "height": int(region.height),
        },
    }
