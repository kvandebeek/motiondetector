from __future__ import annotations

"""
payload_normalize.py

Builds the normalized JSON payload emitted by the monitor/analyzer.

This module deliberately centralizes the wire-format so other parts of the app
(UI/server/clients) can rely on a stable schema.

Notes:
- Timestamps are UNIX epoch seconds (float).
- Numeric fields are explicitly cast to built-in Python types to avoid
  serialization surprises (e.g., numpy scalars).
- Tile keys are currently fixed to 9 entries: tile1..tile9.
"""

from dataclasses import dataclass
from typing import Any
import time


@dataclass(frozen=True)
class RegionPayload:
    """
    Serializable region description of the monitored screen area.

    Kept small and strictly typed because it is sent across process boundaries
    (e.g., to the server/UI).
    """
    monitor_id: int
    x: int
    y: int
    width: int
    height: int


def build_payload(
    *,
    capture_state: str,
    capture_reason: str,
    backend: str,
    video_state: str,
    confidence: float,
    motion_mean: float,
    tiles: tuple[float, ...],
    last_phash_change_ts: float,
    stale: bool,
    stale_age_sec: float,
    region: RegionPayload,
) -> dict[str, Any]:
    """
    Create the normalized payload expected by downstream consumers.

    Args:
        capture_state: Capture subsystem state (e.g., "OK", "ERROR").
        capture_reason: Human-readable reason for capture_state (debuggable string).
        backend: Capture backend identifier (e.g., "WGC", "MSS", etc.).
        video_state: Motion classification state (e.g., "MOTION", "NO_MOTION").
        confidence: Classifier confidence in [0..1] (caller defines semantics).
        motion_mean: Overall/aggregate motion metric (caller defines scale).
        tiles: Per-tile motion metrics (currently expected length == 9).
        last_phash_change_ts: Timestamp (epoch seconds) when pHash last changed.
        stale: Whether the payload is considered stale (no updates recently, etc.).
        stale_age_sec: How long (seconds) the payload has been stale.
        region: Region geometry for the monitored area.

    Returns:
        A dict ready for JSON serialization.
    """
    # Single time source for this payload to keep fields consistent.
    ts = time.time()

    # Map tile metrics to stable JSON keys.
    # Assumption: tiles has 9 elements for a 3x3 grid.
    tiles_map = {f"tile{i+1}": float(tiles[i]) for i in range(9)}

    # Compute an "overall" health/state summary for quick consumer checks.
    # Current rule: anything other than NO_MOTION is "OK".
    overall_ok = video_state != "NO_MOTION"

    return {
        # Emission timestamp for this payload.
        "timestamp": float(ts),
        "capture": {
            # Capture pipeline status and metadata.
            "state": capture_state,
            "reason": capture_reason,
            "backend": backend,
        },
        "video": {
            # Motion classification and metrics.
            "state": video_state,
            "confidence": float(confidence),
            "motion_mean": float(motion_mean),
            # Per-tile motion values (tile1..tile9).
            **tiles_map,
            # Telemetry for "change detection" and staleness.
            "last_phash_change_ts": float(last_phash_change_ts),
            "last_update_ts": float(ts),
            "stale": bool(stale),
            "stale_age_sec": float(stale_age_sec),
        },
        "overall": {
            # High-level status for consumers that don't care about details.
            "state": "OK" if overall_ok else "NOT_OK",
            # Reasons list is structured for future expansion (multiple reasons).
            "reasons": ["ok"] if overall_ok else ["no_motion_all_tiles"],
        },
        # Reserved for structured errors (e.g., capture exceptions, encoder failures).
        "errors": [],
        "region": {
            # Region information is explicitly cast for JSON safety.
            "monitor_id": int(region.monitor_id),
            "x": int(region.x),
            "y": int(region.y),
            "width": int(region.width),
            "height": int(region.height),
        },
    }
