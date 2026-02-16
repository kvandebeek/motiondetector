from __future__ import annotations

import numpy as np
from typing import Dict, List, Optional, Tuple


class VideoMetrics:
    def __init__(self, diff_threshold: float = 0.02) -> None:
        self._prev_gray: Optional[np.ndarray] = None
        self._diff_threshold = diff_threshold

    @staticmethod
    def _to_gray(frame_bgra: np.ndarray) -> np.ndarray:
        b = frame_bgra[:, :, 0].astype(np.uint16)
        g = frame_bgra[:, :, 1].astype(np.uint16)
        r = frame_bgra[:, :, 2].astype(np.uint16)
        y = (77 * r + 150 * g + 29 * b) >> 8
        return y.astype(np.uint8)

    @staticmethod
    def _tile_means(diff: np.ndarray, grid: Tuple[int, int] = (3, 3)) -> List[float]:
        h, w = diff.shape
        rows, cols = grid
        tile_h = h // rows
        tile_w = w // cols
        out: List[float] = []

        for r in range(rows):
            for c in range(cols):
                y0 = r * tile_h
                x0 = c * tile_w
                y1 = (r + 1) * tile_h if r < rows - 1 else h
                x1 = (c + 1) * tile_w if c < cols - 1 else w
                tile = diff[y0:y1, x0:x1]
                out.append(float(tile.mean() / 255.0))

        return out

    def process(self, frame_bgra: np.ndarray) -> Dict:
        gray = self._to_gray(frame_bgra)

        if self._prev_gray is None:
            self._prev_gray = gray
            return self._empty_metrics("warming_up")

        diff = np.abs(
            gray.astype(np.int16) - self._prev_gray.astype(np.int16)
        ).astype(np.uint8)

        self._prev_gray = gray

        tiles = self._tile_means(diff)
        motion_mean = float(diff.mean() / 255.0)

        is_motion = (
            motion_mean >= self._diff_threshold
            or any(t >= self._diff_threshold for t in tiles)
        )

        return {
            "state": "MOTION" if is_motion else "NO_MOTION",
            "confidence": min(1.0, motion_mean * 8.0),
            "motion_mean": motion_mean,
            "tiles": tiles,
        }

    @staticmethod
    def _empty_metrics(reason: str) -> Dict:
        return {
            "state": "ERROR",
            "confidence": 0.0,
            "motion_mean": 0.0,
            "tiles": [0.0] * 9,
            "reason": reason,
        }
