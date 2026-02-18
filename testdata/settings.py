# testdata/settings.py
from __future__ import annotations

from dataclasses import dataclass

from config.config import AppConfig


@dataclass(frozen=True)
class TestDataSettings:
    fps: float
    diff_gain: float
    ema_alpha: float
    no_motion_threshold: float
    low_activity_threshold: float
    mean_full_scale: float
    tile_full_scale: float
    grid_rows: int
    grid_cols: int

    @staticmethod
    def from_config(cfg: AppConfig) -> "TestDataSettings":
        return TestDataSettings(
            fps=float(cfg.fps),
            diff_gain=float(cfg.diff_gain),
            ema_alpha=float(cfg.ema_alpha),
            no_motion_threshold=float(cfg.no_motion_threshold),
            low_activity_threshold=float(cfg.low_activity_threshold),
            mean_full_scale=float(cfg.mean_full_scale),
            tile_full_scale=float(cfg.tile_full_scale),
            grid_rows=int(cfg.grid_rows),
            grid_cols=int(cfg.grid_cols),
        )
