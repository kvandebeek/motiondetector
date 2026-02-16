from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import time


@dataclass(frozen=True)
class RegionPayload:
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
    ts = time.time()

    tiles_map = {f"tile{i+1}": float(tiles[i]) for i in range(9)}

    return {
        "timestamp": float(ts),
        "capture": {
            "state": capture_state,
            "reason": capture_reason,
            "backend": backend,
        },
        "video": {
            "state": video_state,
            "confidence": float(confidence),
            "motion_mean": float(motion_mean),
            **tiles_map,
            "last_phash_change_ts": float(last_phash_change_ts),
            "last_update_ts": float(ts),
            "stale": bool(stale),
            "stale_age_sec": float(stale_age_sec),
        },
        "overall": {
            "state": "OK" if video_state != "NO_MOTION" else "NOT_OK",
            "reasons": ["ok"] if video_state != "NO_MOTION" else ["no_motion_all_tiles"],
        },
        "errors": [],
        "region": {
            "monitor_id": int(region.monitor_id),
            "x": int(region.x),
            "y": int(region.y),
            "width": int(region.width),
            "height": int(region.height),
        },
    }
