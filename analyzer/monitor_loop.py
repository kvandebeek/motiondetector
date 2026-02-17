# analyzer/monitor_loop.py
"""
Monitor loop: capture a screen region at a fixed FPS, compute motion metrics, publish status JSON,
and optionally record clips when motion state meets configured conditions.

Key concepts
- Capture: `ScreenCapturer.grab(region)` returns a BGRA frame (H, W, 4).
- Analysis path: BGRA -> grayscale uint8 -> frame-to-frame abs diff -> per-tile mean motion.
- Aggregation: combine global mean + top-K tile mean into an activity score, smooth with EMA,
  then map to discrete states: NO_MOTION / LOW_ACTIVITY / MOTION.
- Output: write a single latest payload to `StatusStore` (read elsewhere by server/UI).
- Recording: `ClipRecorder` decides when to start/stop writing a video based on state transitions
  and its own cooldown/grace logic.

Notes
- The "analysis_inset_px" shrinks the analysis ROI inward to avoid border artifacts (window chrome,
  anti-aliasing, resize handles, etc.).
- There is an additional heuristic: detect and crop away leading dead (all-zero) rows in the diff
  (typically caused by capture quirks), before tiling.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from analyzer.capture import Region, ScreenCapturer
from analyzer.recorder import ClipRecorder, RecorderConfig
from server.status_store import StatusStore


@dataclass(frozen=True)
class DetectionParams:
    """
    Immutable configuration for motion detection + recording.

    Motion detection parameters
    - fps: target capture/processing rate.
    - diff_gain: multiply the raw normalized mean diff by this factor before scaling.
      Used to compensate for small pixel deltas or low-contrast scenes.
    - no_motion_threshold / low_activity_threshold: thresholds on the EMA-smoothed activity.
      EMA < no_motion_threshold => NO_MOTION
      EMA < low_activity_threshold => LOW_ACTIVITY
      else => MOTION
    - ema_alpha: smoothing factor in [0..1]. Higher => more responsive, lower => more stable.

    Normalization parameters
    - mean_full_scale: value of (mean_raw after diff_gain) that should map to 1.0.
    - tile_full_scale: value of (tile_raw) that should map to 1.0.

    Grid parameters
    - grid_rows / grid_cols: number of tiles for per-tile heatmap / diagnostics.

    Recording parameters (passed through to ClipRecorder)
    - record_enabled: master toggle.
    - record_trigger_state: state string the recorder uses as "trigger" for starting.
      (Semantics are in ClipRecorder; typically start recording when state == MOTION or similar.)
    - record_clip_seconds: maximum clip duration per recording.
    - record_cooldown_seconds: minimum time between clip starts.
    - record_assets_dir: output directory.
    - record_stop_grace_seconds: after trigger ends, keep recording briefly to capture tail.

    Analysis ROI control
    - analysis_inset_px: inset inside the captured region before computing diff/tiles.
      Helps avoid false positives from border noise.
    """
    fps: float
    diff_gain: float
    no_motion_threshold: float
    low_activity_threshold: float
    ema_alpha: float

    # Linear normalization targets: raw value == full_scale -> 1.0
    mean_full_scale: float
    tile_full_scale: float

    # Tile grid
    grid_rows: int
    grid_cols: int

    # Recording
    record_enabled: bool
    record_trigger_state: str  # e.g. "MOTION" or "NO_MOTION" depending on desired behavior
    record_clip_seconds: int
    record_cooldown_seconds: int
    record_assets_dir: str
    record_stop_grace_seconds: int = 10

    # Inset inside the captured region before computing diff/tiles
    analysis_inset_px: int = 10


def _to_gray_u8(frame_bgra: np.ndarray) -> np.ndarray:
    """
    Convert BGRA uint8 frame to grayscale uint8.

    Uses integer luma approximation (roughly ITU-R BT.601):
      Y ≈ 0.299 R + 0.587 G + 0.114 B
    Implemented with integer weights to avoid float cost in the hot path.
    """
    if frame_bgra.ndim != 3 or frame_bgra.shape[2] != 4:
        raise ValueError(f"Expected BGRA frame (H,W,4), got {frame_bgra.shape}")

    # Promote to uint16 to prevent overflow during weighted sum.
    b = frame_bgra[:, :, 0].astype(np.uint16)
    g = frame_bgra[:, :, 1].astype(np.uint16)
    r = frame_bgra[:, :, 2].astype(np.uint16)

    # 77/256 ~= 0.3008, 150/256 ~= 0.5859, 29/256 ~= 0.1133
    y = (77 * r + 150 * g + 29 * b) >> 8
    return y.astype(np.uint8)


def _bgra_to_bgr(frame_bgra: np.ndarray) -> np.ndarray:
    """
    Drop alpha channel to obtain BGR frame copy for recording via OpenCV.
    """
    if frame_bgra.ndim != 3 or frame_bgra.shape[2] != 4:
        raise ValueError(f"Expected BGRA frame (H,W,4), got {frame_bgra.shape}")
    return frame_bgra[:, :, :3].copy()


def _clamp01(x: float) -> float:
    """
    Clamp a float into [0.0, 1.0] without numpy overhead.
    """
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _edges(size: int, parts: int) -> List[int]:
    """
    Compute monotonically non-decreasing integer edges for splitting [0..size] into `parts` bins.

    The rounding strategy is symmetric and stable:
    - edges are computed with round(i * size / parts)
    - first edge forced to 0, last forced to size
    - monotonicity enforced so empty tiles do not create negative slices
    """
    if size < 0:
        raise ValueError("size must be >= 0")
    if parts <= 0:
        raise ValueError("parts must be > 0")

    out = [int(round(i * size / parts)) for i in range(parts + 1)]
    out[0] = 0
    out[parts] = int(size)

    # Ensure edges never go backwards due to rounding artifacts.
    for i in range(1, len(out)):
        if out[i] < out[i - 1]:
            out[i] = out[i - 1]
    return out


def _tile_means(diff_u8: np.ndarray, *, rows: int, cols: int) -> List[float]:
    """
    Split `diff_u8` into an rows x cols grid and return per-tile mean(diff)/255.0.

    Returns a flat list in row-major order:
      [r0c0, r0c1, ..., r0cN, r1c0, ..., rM cN]
    """
    if rows <= 0 or cols <= 0:
        raise ValueError("grid_rows and grid_cols must be > 0")

    h, w = diff_u8.shape
    x_edges = _edges(w, cols)
    y_edges = _edges(h, rows)

    out: List[float] = []
    for r in range(rows):
        y0 = y_edges[r]
        y1 = y_edges[r + 1]
        for c in range(cols):
            x0 = x_edges[c]
            x1 = x_edges[c + 1]

            tile = diff_u8[y0:y1, x0:x1]
            # A tile can be empty if edges coincide; treat it as no motion.
            out.append(float(tile.mean() / 255.0) if tile.size else 0.0)
    return out


def _topk_mean(values: List[float], k: int) -> float:
    """
    Mean of the top-K values (K>=1) from a list.

    Used to make the detector sensitive to a small but strong local motion region.
    """
    if not values:
        return 0.0
    kk = max(1, min(int(k), len(values)))
    s = sorted(values, reverse=True)[:kk]
    return float(sum(s) / float(len(s)))


def _tiles_named(tiles: List[float], *, rows: int, cols: int) -> Dict[str, float]:
    """
    Return a dict mapping "t1".."tN" to tile values for easier JSON consumption.
    """
    n = int(rows) * int(cols)
    if len(tiles) != n:
        raise ValueError(f"tiles length {len(tiles)} != rows*cols {n} (rows={rows}, cols={cols})")
    return {f"t{i + 1}": float(tiles[i]) for i in range(n)}


def _detect_dead_top_rows(diff_u8: np.ndarray, *, rows: int, max_rows: int = 5) -> Tuple[int, float, float, float]:
    """
    Heuristic: detect leading (top) bands that are fully zero in the diff image.

    Motivation
    - Some capture pipelines can yield a strip of static/invalid pixels that never changes.
    - If included in tiling, that strip can skew tile boundaries or dilute motion intensity.

    Approach
    - Define a band height ~ (H // rows) so bands align approximately with tile row height.
    - Count consecutive zero-mean bands from the top, up to `max_rows` (and < rows).
    - Also return first 3 band means as quick diagnostics.

    Returns
    - dead: number of dead top bands
    - b1, b2, b3: mean(diff) for bands 0..2 (uint8 domain, 0..255)
    """
    h = int(diff_u8.shape[0])
    tile_h = max(1, h // max(rows, 1))

    def band_mean(i: int) -> float:
        y0 = i * tile_h
        y1 = min(h, (i + 1) * tile_h)
        if y0 >= h or y1 <= y0:
            return 0.0
        return float(diff_u8[y0:y1, :].mean())

    b1 = band_mean(0)
    b2 = band_mean(1)
    b3 = band_mean(2)

    limit = max(0, min(int(max_rows), rows - 1))
    dead = 0
    for i in range(limit):
        if band_mean(i) == 0.0:
            dead += 1
        else:
            break

    return dead, b1, b2, b3


def _apply_inset(gray: np.ndarray, inset_px: int) -> Tuple[np.ndarray, Dict[str, int]]:
    """
    Crop inward by `inset_px` on all sides.

    Returns
    - roi: cropped grayscale image (or original if inset is 0 or would collapse ROI)
    - rect: dict describing ROI in original gray coordinates (x,y,width,height)

    If inset is too large (would result in empty/degenerate ROI), returns the original gray.
    """
    inset = max(0, int(inset_px))
    if inset == 0:
        h, w = gray.shape
        return gray, {"x": 0, "y": 0, "width": int(w), "height": int(h)}

    h, w = gray.shape
    x0 = min(w, inset)
    y0 = min(h, inset)
    x1 = max(x0, w - inset)
    y1 = max(y0, h - inset)

    # If inset consumes the full image, fall back to full frame.
    if x1 - x0 < 1 or y1 - y0 < 1:
        return gray, {"x": 0, "y": 0, "width": int(w), "height": int(h)}

    roi = gray[y0:y1, x0:x1]
    return roi, {"x": int(x0), "y": int(y0), "width": int(x1 - x0), "height": int(y1 - y0)}


class MonitorLoop:
    """
    Runs the capture+analysis loop on a background thread.

    Responsibilities
    - Capture frames at approximately `params.fps`.
    - Compute motion metrics and push a payload into `StatusStore`.
    - Drive `ClipRecorder` with current state and BGR frames.
    - Clean up recorder and capturer thread resources on exit.

    Threading model
    - `.start()` creates a daemon thread.
    - `.stop()` signals the loop to exit.
    - `.join(timeout)` waits for completion.
    """

    def __init__(
        self,
        *,
        store: StatusStore,
        capturer: ScreenCapturer,
        params: DetectionParams,
        get_region: Callable[[], Region],
    ) -> None:
        self._store = store
        self._capturer = capturer
        self._params = params
        self._get_region = get_region

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Previous grayscale frame (after inset) to compute diff against.
        self._prev_gray: Optional[np.ndarray] = None

        # EMA of activity (global scalar used for state mapping and confidence).
        self._ema_activity: float = 0.0

        # Timestamp of the last successful process update.
        self._last_update_ts: float = 0.0

        # Last computed discrete state; kept for potential future logic and debugging.
        self._prev_state: str = "ERROR"

        # Recorder configuration is derived from params once at startup.
        self._recorder = ClipRecorder(
            RecorderConfig(
                enabled=bool(params.record_enabled),
                trigger_state=str(params.record_trigger_state),
                clip_seconds=int(params.record_clip_seconds),
                cooldown_seconds=int(params.record_cooldown_seconds),
                fps=float(params.fps),
                assets_dir=str(params.record_assets_dir),
                stop_grace_seconds=int(getattr(params, "record_stop_grace_seconds", 10)),
            )
        )

    def start(self) -> None:
        """
        Start the background thread (idempotent if already running).
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="motiondetector-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """
        Signal the loop to stop at the next opportunity.
        """
        self._stop.set()

    def join(self, timeout: float) -> None:
        """
        Wait for the loop thread to exit.
        """
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        """
        Main worker loop.

        Timing
        - Compute a target period = 1/fps.
        - Each iteration measures elapsed processing time and sleeps the remainder.
        - Uses Event.wait(timeout=...) so stop can interrupt sleeps promptly.
        """
        period = 1.0 / max(float(self._params.fps), 1.0)

        try:
            while not self._stop.is_set():
                t0 = time.time()
                region = self._get_region()

                # Capture failures are handled per-frame; we keep looping.
                try:
                    frame = self._capturer.grab(region)
                except Exception as e:
                    self._store.set_latest(self._error_payload(reason=str(e), region=region))
                    self._stop.wait(timeout=0.05)
                    continue

                # Processing failures also become an error payload but do not crash the loop.
                try:
                    payload = self._process_frame(frame=frame, ts=t0, region=region)
                    self._store.set_latest(payload)
                except Exception as e:
                    self._store.set_latest(self._error_payload(reason=f"process_failed: {e}", region=region))

                elapsed = time.time() - t0
                sleep_for = period - elapsed
                if sleep_for > 0:
                    self._stop.wait(timeout=sleep_for)
        finally:
            # Best-effort cleanup; never raise during shutdown.
            try:
                self._recorder.stop()
            except Exception:
                pass
            try:
                self._capturer.close_thread_resources()
            except Exception:
                pass

    def _process_frame(self, *, frame: np.ndarray, ts: float, region: Region) -> Dict:
        """
        Convert frame to analysis representation, compute motion, update recorder, and build payload.

        Output payload schema (high level)
        - timestamp
        - capture: {state, reason, backend}
        - video: {state, confidence, motion_mean, grid, tiles, tiles_named, ...debug}
        - overall: {state, reasons}
        - errors
        - region
        """
        gray_full = _to_gray_u8(frame)

        rows = int(self._params.grid_rows)
        cols = int(self._params.grid_cols)

        # Crop away an inset border to reduce false motion at edges.
        gray, inset_rect = _apply_inset(gray_full, int(getattr(self._params, "analysis_inset_px", 10)))

        # Warm-up: we need a previous frame to diff against.
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            self._last_update_ts = ts
            self._prev_state = "ERROR"

            payload = self._status_payload(
                ts=ts,
                region=region,
                video_state="ERROR",
                confidence=0.0,
                motion_mean=0.0,
                tiles=[0.0] * (rows * cols),
                rows=rows,
                cols=cols,
                overall_state="NOT_OK",
                overall_reasons=["warming_up"],
                errors=["warming_up"],
            )
            payload["video"]["debug"] = {"analysis_inset_rect": inset_rect}
            return payload

        # Frame-to-frame absolute difference (uint8).
        diff = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16)).astype(np.uint8)
        self._prev_gray = gray
        self._last_update_ts = ts

        # Detect and optionally crop dead diff bands at the top.
        dead_rows, b1, b2, b3 = _detect_dead_top_rows(diff, rows=rows, max_rows=5)
        print("bands:", b1, b2, b3, "dead_rows:", dead_rows)

        if dead_rows > 0:
            # Compute crop based on approximate tile-row height so cropping aligns with grid logic.
            tile_h = max(1, diff.shape[0] // max(rows, 1))
            crop_top = min(diff.shape[0] - 1, dead_rows * tile_h)

            diff_roi = diff[crop_top:, :]
            roi_rect = {"x": 0, "y": int(crop_top), "width": int(diff.shape[1]), "height": int(diff_roi.shape[0])}
        else:
            diff_roi = diff
            roi_rect = {"x": 0, "y": 0, "width": int(diff.shape[1]), "height": int(diff.shape[0])}

        # Per-tile normalized means (0..1).
        tiles_raw = _tile_means(diff_roi, rows=rows, cols=cols)

        # Global normalized mean (0..1) for the ROI.
        mean_raw = float(diff_roi.mean() / 255.0)

        # Gain compensation (still clamped to <= 1 before the full-scale mapping).
        mean_raw = float(min(1.0, mean_raw * float(self._params.diff_gain)))

        # Map raw values to [0..1] using full-scale calibration.
        mean_full = float(self._params.mean_full_scale)
        tile_full = float(self._params.tile_full_scale)
        if mean_full <= 0.0:
            raise ValueError("mean_full_scale must be > 0")
        if tile_full <= 0.0:
            raise ValueError("tile_full_scale must be > 0")

        mean_norm = _clamp01(mean_raw / mean_full)
        tiles_norm = [_clamp01(t / tile_full) for t in tiles_raw]

        # Activity combines "overall" motion + "local peak" motion.
        # - topk_mean(k=1) makes a single strong tile matter.
        # - max(mean_norm, topk_mean) prevents dilution when motion is localized.
        topk_mean = _topk_mean(tiles_norm, 1)
        activity = max(mean_norm, topk_mean)

        # Smooth with EMA to reduce flicker.
        a = float(self._params.ema_alpha)
        self._ema_activity = (a * activity) + ((1.0 - a) * self._ema_activity)

        # Discrete state mapping.
        if self._ema_activity < float(self._params.no_motion_threshold):
            video_state = "NO_MOTION"
        elif self._ema_activity < float(self._params.low_activity_threshold):
            video_state = "LOW_ACTIVITY"
        else:
            video_state = "MOTION"

        # Confidence is currently just the EMA clamped to [0..1].
        confidence = _clamp01(self._ema_activity)

        # overall is a simplified OK/NOT_OK flag used by your app UI/server logic.
        overall_state = "OK" if video_state == "MOTION" else "NOT_OK"
        overall_reasons: List[str] = [] if video_state == "MOTION" else ["no_motion_all_tiles"]

        payload = self._status_payload(
            ts=ts,
            region=region,
            video_state=video_state,
            confidence=confidence,
            motion_mean=float(self._ema_activity),
            tiles=tiles_norm,
            rows=rows,
            cols=cols,
            overall_state=overall_state,
            overall_reasons=overall_reasons,
            errors=[],
        )

        # Additional fields that can help downstream debugging/visualization.
        payload["video"]["last_update_ts"] = float(self._last_update_ts)
        payload["video"]["stale"] = False
        payload["video"]["stale_age_sec"] = 0.0
        payload["video"]["debug"] = {
            "analysis_inset_px": int(getattr(self._params, "analysis_inset_px", 10)),
            "analysis_inset_rect": inset_rect,
            "bands_u8": [float(b1), float(b2), float(b3)],
            "dead_top_tile_rows": int(dead_rows),
            "diff_roi_rect": roi_rect,
        }

        # Recording path expects BGR frames.
        frame_bgr = _bgra_to_bgr(frame)
        self._recorder.update(now_ts=ts, state=video_state, frame_bgr=frame_bgr)

        self._prev_state = video_state
        return payload

    @staticmethod
    def _status_payload(
        *,
        ts: float,
        region: Region,
        video_state: str,
        confidence: float,
        motion_mean: float,
        tiles: List[float],
        rows: int,
        cols: int,
        overall_state: str,
        overall_reasons: List[str],
        errors: List[str],
    ) -> Dict:
        """
        Build the standard OK payload.

        The payload is designed to be JSON-serializable:
        - all numpy scalar types are cast to Python floats/ints
        - region is repeated to make the payload self-contained
        """
        tiles_list: List[float] = [float(x) for x in tiles]

        return {
            "timestamp": float(ts),
            "capture": {"state": "OK", "reason": "ok", "backend": "MSS"},
            "video": {
                "state": video_state,
                "confidence": float(confidence),
                "motion_mean": float(motion_mean),
                "grid": {"rows": int(rows), "cols": int(cols)},
                "tiles": tiles_list,
                "tiles_named": _tiles_named(tiles_list, rows=int(rows), cols=int(cols)),
                # Placeholder fields for future features (e.g. perceptual hash changes).
                "last_phash_change_ts": 0.0,
                "last_update_ts": float(ts),
                "stale": False,
                "stale_age_sec": 0.0,
            },
            "overall": {"state": overall_state, "reasons": list(overall_reasons)},
            "errors": list(errors),
            "region": {"x": int(region.x), "y": int(region.y), "width": int(region.width), "height": int(region.height)},
        }

    @staticmethod
    def _error_payload(*, reason: str, region: Region) -> Dict:
        """
        Build a standard ERROR payload when capture/processing fails.
        """
        now = time.time()
        return {
            "timestamp": float(now),
            "capture": {"state": "ERROR", "reason": reason, "backend": "MSS"},
            "video": {
                "state": "ERROR",
                "confidence": 0.0,
                "motion_mean": 0.0,
                "grid": {"rows": 0, "cols": 0},
                "tiles": [],
                "tiles_named": {},
                "last_phash_change_ts": 0.0,
                "last_update_ts": 0.0,
                "stale": True,
                "stale_age_sec": 0.0,
            },
            "overall": {"state": "NOT_OK", "reasons": ["capture_error"]},
            "errors": [reason],
            "region": {"x": int(region.x), "y": int(region.y), "width": int(region.width), "height": int(region.height)},
        }
