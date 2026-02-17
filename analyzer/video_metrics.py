from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np


class VideoMetrics:
    """
    Compute simple motion metrics between consecutive frames.

    Approach:
      1) Convert the incoming BGRA frame to an 8-bit grayscale luminance image.
      2) Compute absolute per-pixel difference vs the previous grayscale frame.
      3) Aggregate the difference into:
         - a global mean (motion_mean)
         - per-tile means over a grid (default 3x3 -> 9 tiles)
      4) Declare MOTION if either the global mean or any tile mean exceeds diff_threshold.
    """

    def __init__(self, diff_threshold: float = 0.02) -> None:
        # Previous frame in grayscale (uint8). None until the first frame arrives.
        self._prev_gray: Optional[np.ndarray] = None

        # Motion threshold in normalized units [0..1] where 1.0 means full-scale diff (255).
        self._diff_threshold = diff_threshold

    @staticmethod
    def _to_gray(frame_bgra: np.ndarray) -> np.ndarray:
        """
        Convert a BGRA uint8 frame to grayscale uint8 luminance.

        The formula is an integer approximation of ITU-R BT.601 luma:
          Y ≈ 0.299*R + 0.587*G + 0.114*B
        Implemented with integer weights and a right shift for speed:
          Y = (77*R + 150*G + 29*B) >> 8

        Notes:
        - Alpha channel is ignored.
        - Intermediate uses uint16 to prevent overflow during multiplication/summation.
        """
        b = frame_bgra[:, :, 0].astype(np.uint16)
        g = frame_bgra[:, :, 1].astype(np.uint16)
        r = frame_bgra[:, :, 2].astype(np.uint16)
        y = (77 * r + 150 * g + 29 * b) >> 8
        return y.astype(np.uint8)

    @staticmethod
    def _tile_means(diff: np.ndarray, grid: Tuple[int, int] = (3, 3)) -> List[float]:
        """
        Compute normalized mean motion per tile over a (rows, cols) grid.

        Input:
          diff: 2D uint8 array (absolute difference image) with values in [0..255].
          grid: (rows, cols). Default (3,3) produces 9 values in row-major order.

        Tiling strategy:
          - Use floor division to define a base tile size.
          - Let the last row/col absorb any remainder so the full image is covered.

        Output:
          List[float] where each entry is mean(diff_tile)/255.0 in [0..1].
        """
        h, w = diff.shape
        rows, cols = grid

        # Base tile size; last tiles extend to the edge to cover any remainder pixels.
        tile_h = h // rows
        tile_w = w // cols

        out: List[float] = []

        for r in range(rows):
            for c in range(cols):
                # Tile bounds (y0:y1, x0:x1) in pixel coordinates.
                y0 = r * tile_h
                x0 = c * tile_w
                y1 = (r + 1) * tile_h if r < rows - 1 else h
                x1 = (c + 1) * tile_w if c < cols - 1 else w

                tile = diff[y0:y1, x0:x1]

                # Normalize by 255 so thresholds and outputs live in [0..1].
                out.append(float(tile.mean() / 255.0))

        return out

    def process(self, frame_bgra: np.ndarray) -> Dict:
        """
        Process a single BGRA frame and return motion metrics.

        Returns a dict containing:
          - state: "MOTION" | "NO_MOTION" | "ERROR"
          - confidence: heuristic confidence in [0..1]
          - motion_mean: global normalized mean absolute difference in [0..1]
          - tiles: list of 9 normalized tile means in [0..1]
          - reason: only present in ERROR (e.g. "warming_up")
        """
        gray = self._to_gray(frame_bgra)

        # First frame: we don't have a baseline yet; store and report a warmup error.
        if self._prev_gray is None:
            self._prev_gray = gray
            return self._empty_metrics("warming_up")

        # Compute absolute difference image.
        # Cast to int16 to avoid uint8 underflow on subtraction.
        diff = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16)).astype(
            np.uint8
        )

        # Update baseline for next call.
        self._prev_gray = gray

        # Aggregate per-tile and global mean, all normalized to [0..1].
        tiles = self._tile_means(diff)
        motion_mean = float(diff.mean() / 255.0)

        # Motion decision: either the global mean exceeds the threshold,
        # or any tile exceeds it (more sensitive to localized motion).
        is_motion = motion_mean >= self._diff_threshold or any(
            t >= self._diff_threshold for t in tiles
        )

        return {
            "state": "MOTION" if is_motion else "NO_MOTION",
            # Confidence is a simple scaling of the global mean; clamps at 1.0.
            # This is a heuristic, not a calibrated probability.
            "confidence": min(1.0, motion_mean * 8.0),
            "motion_mean": motion_mean,
            "tiles": tiles,
        }

    @staticmethod
    def _empty_metrics(reason: str) -> Dict:
        """
        Return a consistent "no data / error" payload.

        Used for warmup (first frame) and can be reused for other error states.
        tiles defaults to 3x3 (9 values) to keep output shape stable for consumers.
        """
        return {
            "state": "ERROR",
            "confidence": 0.0,
            "motion_mean": 0.0,
            "tiles": [0.0] * 9,
            "reason": reason,
        }
