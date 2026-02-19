# File commentary: testdata/summary.py - This file holds logic used by the motion detector project.
# testdata/summary.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import csv


@dataclass
class SceneStats:
    scene_index: int
    scene_name: str
    phase_name: str
    expected_state: str

    frames: int = 0
    match_frames: int = 0
    fp: int = 0
    fn: int = 0

    motion_mean_sum: float = 0.0
    motion_mean_max: float = 0.0

    tile_max_sum: float = 0.0
    tile_max_max: float = 0.0


class TestDataSummaryWriter:
    def __init__(self, *, log_dir: str = "./testdata_logs") -> None:
        """Initialize this object with the provided inputs and prepare its internal state."""
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._path = d / f"testdata_summary_{stamp}.csv"
        self._fh = self._path.open("w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(
            [
                "ts_iso",
                "scene_index",
                "scene_name",
                "phase_name",
                "expected_state",
                "frames",
                "match_pct",
                "fp",
                "fn",
                "motion_mean_avg",
                "motion_mean_max",
                "tile_max_avg",
                "tile_max_max",
            ]
        )
        self._fh.flush()

    @property
    def path_str(self) -> str:
        """Handle path str for this module."""
        return str(self._path)

    def write(self, s: SceneStats) -> None:
        """Write one record to the output destination used by this component."""
        if s.frames <= 0:
            return
        ts_iso = datetime.now(timezone.utc).isoformat()
        match_pct = 100.0 * (float(s.match_frames) / float(s.frames))
        mm_avg = s.motion_mean_sum / float(s.frames)
        tm_avg = s.tile_max_sum / float(s.frames)

        self._w.writerow(
            [
                ts_iso,
                s.scene_index,
                s.scene_name,
                s.phase_name,
                s.expected_state,
                s.frames,
                f"{match_pct:.2f}",
                s.fp,
                s.fn,
                f"{mm_avg:.6g}",
                f"{s.motion_mean_max:.6g}",
                f"{tm_avg:.6g}",
                f"{s.tile_max_max:.6g}",
            ]
        )
        self._fh.flush()

    def close(self) -> None:
        """Close open resources so files/handles are safely released."""
        try:
            self._fh.flush()
        finally:
            self._fh.close()
