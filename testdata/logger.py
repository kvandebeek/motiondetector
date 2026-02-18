# testdata/logger.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import csv



@dataclass(frozen=True)
class TestDataLogRow:
    ts_iso: str
    scene_index: int
    scene_name: str
    scene_time_s: float

    expected_state: str

    # "output_value" (generator-side metric)
    output_value: float  # engine EMA activity (0..1-ish)

    # "detection_value" (detector-side metric)
    detection_value: Optional[float]  # /status.video.motion_mean (0..1-ish), may be missing
    confidence: Optional[float]

    actual_state: Optional[str]  # /status.video.state
    match: Optional[bool]        # actual == expected, if actual present

    # Useful context for tuning:
    diff_gain: float
    ema_alpha: float
    no_motion_threshold: float
    low_activity_threshold: float
    mean_full_scale: float
    fps: float


class TestDataLogger:
    def __init__(self, *, log_dir: str = "./testdata_logs") -> None:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._path = d / f"testdata_{stamp}.csv"
        self._fh = self._path.open("w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)

        self._w.writerow(
            [
                "ts_iso",
                "scene_index",
                "scene_name",
                "scene_time_s",
                "expected_state",
                "output_value",
                "detection_value",
                "confidence",
                "actual_state",
                "match",
                "diff_gain",
                "ema_alpha",
                "no_motion_threshold",
                "low_activity_threshold",
                "mean_full_scale",
                "fps",
            ]
        )
        self._fh.flush()

    @property
    def path_str(self) -> str:
        return str(self._path)

    def write(self, row: TestDataLogRow) -> None:
        if(row.actual_state != row.expected_state):
            self._w.writerow(
                [
                    row.ts_iso,
                    row.scene_index,
                    row.scene_name,
                    f"{row.scene_time_s:.3f}",
                    row.expected_state,
                    f"{row.output_value:.6g}",
                    "" if row.detection_value is None else f"{row.detection_value:.6g}",
                    "" if row.confidence is None else f"{row.confidence:.6g}",
                    "" if row.actual_state is None else row.actual_state,
                    "" if row.match is None else ("1" if row.match else "0"),
                    f"{row.diff_gain:.6g}",
                    f"{row.ema_alpha:.6g}",
                    f"{row.no_motion_threshold:.6g}",
                    f"{row.low_activity_threshold:.6g}",
                    f"{row.mean_full_scale:.6g}",
                    f"{row.fps:.6g}",
                ]
            )
            self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.flush()
        finally:
            self._fh.close()
