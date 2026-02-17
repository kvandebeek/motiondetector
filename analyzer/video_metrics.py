from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np


class VideoMetrics:
    """
    Compute simple motion metrics between consecutive frames.

    High-level algorithm:
      1) Convert the incoming BGRA frame to an 8-bit grayscale luminance image.
      2) Compute absolute per-pixel difference vs the previous grayscale frame.
      3) Aggregate the difference into:
         - a global mean (motion_mean)
         - per-tile means over a fixed grid (default 3x3 -> 9 tiles)
      4) Declare MOTION if either the global mean or any tile mean exceeds diff_threshold.

    Important operational detail (DPI / multi-monitor / region moves):
      - If the capture region moves across displays (different DPI scaling) or is resized,
        the captured frame shape can change. Diffing across different shapes produces
        incorrect/shifted motion signals.
      - This class detects shape changes and resets its baseline (prev frame) to avoid
        false motion.

    Output stability:
      - Tile list is always 9 entries by default (3x3) so consumers can render a consistent grid.
      - Warmup/shape-change conditions return an ERROR state with a reason, rather than raising.
    """

    def __init__(self, diff_threshold: float = 0.02) -> None:
        # Previous grayscale frame (uint8). None until the first frame is processed.
        self._prev_gray: Optional[np.ndarray] = None

        # Motion threshold in normalized units [0..1], where 1.0 corresponds to a full-scale
        # per-pixel difference (255). Typical values are small because most frames differ lightly.
        self._diff_threshold = float(diff_threshold)

        # Lightweight debug/telemetry fields:
        # - frame_idx is useful for sampling logs at a fixed cadence
        # - shape_reset_count helps diagnose unexpected monitor/DPI/region changes
        self._frame_idx: int = 0
        self._shape_reset_count: int = 0
        self._last_shape: Optional[Tuple[int, int]] = None  # (h, w)

    @staticmethod
    def _to_gray(frame_bgra: np.ndarray) -> np.ndarray:
        """
        Convert a BGRA uint8 frame to grayscale uint8 luminance.

        Uses an integer approximation of ITU-R BT.601 luma:
          Y ≈ 0.299*R + 0.587*G + 0.114*B
        Implemented with integer weights and a right shift:
          Y = (77*R + 150*G + 29*B) >> 8

        Notes:
        - Alpha is ignored.
        - uint16 intermediates prevent overflow during multiplication/summation.
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
          diff: 2D uint8 absolute-difference image with values in [0..255].
          grid: (rows, cols). Default (3,3) produces 9 values in row-major order.

        Tiling strategy:
          - Compute a base tile size via floor division.
          - Let the last row and last column absorb any remainder so coverage is complete.
            This avoids gaps when dimensions aren't divisible by the grid size.

        Output:
          List[float] where each entry is mean(diff_tile)/255.0 in [0..1].
        """
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
        """
        Process one BGRA frame and return motion metrics.

        Returns a dict with:
          - state: "MOTION" | "NO_MOTION" | "ERROR"
          - confidence: heuristic confidence in [0..1]
          - motion_mean: global normalized mean absolute difference in [0..1]
          - tiles: list of 9 normalized tile means in [0..1]
          - reason: only present for "ERROR" warmup/reset cases

        Error-state policy:
          - “ERROR” here does not necessarily mean a fatal error; it means “no valid diff yet”.
            Consumers can treat it as “warming up / baseline reset”.
        """
        self._frame_idx += 1

        # Convert frame to a single-channel grayscale image for stable diffing.
        gray = self._to_gray(frame_bgra)
        h, w = int(gray.shape[0]), int(gray.shape[1])
        shape = (h, w)

        # Periodic heartbeat log to help diagnose runtime behavior without spamming output.
        if (self._frame_idx % 120) == 1:
            print(
                "[metrics]",
                "frame_idx=",
                self._frame_idx,
                "shape=",
                shape,
                "prev_shape=",
                (None if self._last_shape is None else self._last_shape),
                "shape_resets=",
                self._shape_reset_count,
                "diff_threshold=",
                self._diff_threshold,
                flush=True,
            )
        self._last_shape = shape

        # First frame: store baseline and report warmup; cannot compute diff yet.
        if self._prev_gray is None:
            self._prev_gray = gray
            return self._empty_metrics("warming_up")

        # If geometry changes, never diff across mismatched shapes.
        # This prevents false motion caused by shifted/scaled frames.
        prev_h, prev_w = int(self._prev_gray.shape[0]), int(self._prev_gray.shape[1])
        if (prev_h, prev_w) != shape:
            self._shape_reset_count += 1
            print(
                "[metrics]",
                "shape changed -> reset baseline",
                "frame_idx=",
                self._frame_idx,
                "prev=",
                (prev_h, prev_w),
                "now=",
                shape,
                "resets=",
                self._shape_reset_count,
                flush=True,
            )
            self._prev_gray = gray
            return self._empty_metrics("shape_changed")

        # Compute absolute difference. int16 prevents underflow on subtraction.
        diff = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16)).astype(np.uint8)
        self._prev_gray = gray

        # Aggregate the diff into per-tile and global motion metrics.
        tiles = self._tile_means(diff)
        motion_mean = float(diff.mean() / 255.0)

        # Motion is triggered if global mean or any tile exceeds threshold.
        is_motion = motion_mean >= self._diff_threshold or any(t >= self._diff_threshold for t in tiles)

        # Optional evidence logging: only on motion and throttled.
        if is_motion and (self._frame_idx % 30) == 0:
            try:
                max_tile = max(tiles) if tiles else 0.0
                max_idx = int(np.argmax(np.array(tiles))) if tiles else -1
            except Exception:
                max_tile = 0.0
                max_idx = -1
            print(
                "[metrics]",
                "motion",
                "frame_idx=",
                self._frame_idx,
                "motion_mean=",
                round(motion_mean, 5),
                "max_tile=",
                round(float(max_tile), 5),
                "max_tile_idx=",
                max_idx,
                flush=True,
            )

        # Confidence is a simple heuristic derived from the global mean.
        # (If consumers need calibrated confidence, that logic should live at the classifier level.)
        return {
            "state": "MOTION" if is_motion else "NO_MOTION",
            "confidence": min(1.0, motion_mean * 8.0),
            "motion_mean": motion_mean,
            "tiles": tiles,
        }

    @staticmethod
    def _empty_metrics(reason: str) -> Dict:
        """
        Return a consistent "no valid diff yet" payload.

        Used for:
        - warming_up: first frame processed
        - shape_changed: baseline reset due to capture geometry change

        Output shape stability:
        - tiles is always 9 values (3x3) so UIs don't need to special-case rendering.
        """
        return {
            "state": "ERROR",
            "confidence": 0.0,
            "motion_mean": 0.0,
            "tiles": [0.0] * 9,
            "reason": str(reason),
        }
