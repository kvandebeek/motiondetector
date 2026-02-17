# analyzer/monitor_loop.py
"""
Monitor loop: capture a screen region at a fixed FPS, compute motion metrics, publish status JSON,
and optionally record clips when motion state meets configured conditions.

Design goals:
- Deterministic cadence: run a best-effort fixed FPS loop without busy-waiting.
- Robustness: never crash the thread on transient capture/processing failures; publish error payloads instead.
- Stable JSON contract: emit a consistent structure for the UI/clients.
- Mixed-DPI safety: operate in the capturer’s virtual-desktop coordinate system (handled in capture.py).

Output JSON (relevant-only)
- timestamp
- capture: { state, reason, backend }
- video: {
    state, confidence, motion_mean,
    grid: { rows, cols },
    tiles: [float | null, ...] (row-major),
    disabled_tiles: [int, ...] (0-based indices),
    stale, stale_age_sec
  }
- overall: { state, reasons }
- errors: []
- region: { x, y, width, height }
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple, Set

import numpy as np

from analyzer.capture import Region, ScreenCapturer
from analyzer.recorder import ClipRecorder, RecorderConfig
from server.status_store import StatusStore


@dataclass(frozen=True)
class DetectionParams:
    """
    Runtime-configurable motion detection settings.

    Notes:
    - Most fields map directly from config.json and are treated as immutable for the lifetime
      of a MonitorLoop instance.
    - `mean_full_scale` / `tile_full_scale` define a linear normalization: raw == full_scale => 1.0.
    - `analysis_inset_px` allows ignoring borders (window chrome, shadows, overlay edges) that
      frequently cause spurious diffs.
    """
    fps: float
    diff_gain: float
    no_motion_threshold: float
    low_activity_threshold: float
    ema_alpha: float

    mean_full_scale: float
    tile_full_scale: float

    grid_rows: int
    grid_cols: int

    record_enabled: bool
    record_trigger_state: str
    record_clip_seconds: int
    record_cooldown_seconds: int
    record_assets_dir: str
    record_stop_grace_seconds: int = 10

    analysis_inset_px: int = 10


def _to_gray_u8(frame_bgra: np.ndarray) -> np.ndarray:
    """
    Convert BGRA uint8 frame (H,W,4) to grayscale uint8 (H,W).

    Implementation:
    - Uses an integer approximation of BT.601 luma:
      Y ≈ (0.299 R + 0.587 G + 0.114 B)
    - Performed in uint16 to avoid overflow before shifting back to 8-bit.
    """
    if frame_bgra.ndim != 3 or frame_bgra.shape[2] != 4:
        raise ValueError(f"Expected BGRA frame (H,W,4), got {frame_bgra.shape}")

    b = frame_bgra[:, :, 0].astype(np.uint16)
    g = frame_bgra[:, :, 1].astype(np.uint16)
    r = frame_bgra[:, :, 2].astype(np.uint16)

    y = (77 * r + 150 * g + 29 * b) >> 8
    return y.astype(np.uint8)


def _bgra_to_bgr(frame_bgra: np.ndarray) -> np.ndarray:
    """
    Convert BGRA to BGR for OpenCV VideoWriter.

    We `copy()` to ensure a contiguous array independent of the original buffer.
    """
    if frame_bgra.ndim != 3 or frame_bgra.shape[2] != 4:
        raise ValueError(f"Expected BGRA frame (H,W,4), got {frame_bgra.shape}")
    return frame_bgra[:, :, :3].copy()


def _clamp01(x: float) -> float:
    """Clamp a float to [0.0, 1.0]."""
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _edges(size: int, parts: int) -> List[int]:
    """
    Build monotonically non-decreasing edge indices that partition `size` into `parts`.

    Why this exists:
    - When size is not divisible by parts, naive integer division creates uneven coverage
      and can leave off-by-one gaps/overlaps.
    - We use rounding of proportional positions to distribute remainder more evenly.
    - The monotonic fix-up avoids pathological rounding where edges go backwards.
    """
    if size < 0:
        raise ValueError("size must be >= 0")
    if parts <= 0:
        raise ValueError("parts must be > 0")

    out = [int(round(i * size / parts)) for i in range(parts + 1)]
    out[0] = 0
    out[parts] = int(size)

    for i in range(1, len(out)):
        if out[i] < out[i - 1]:
            out[i] = out[i - 1]
    return out


def _tile_means(diff_u8: np.ndarray, *, rows: int, cols: int) -> List[float]:
    """
    Compute per-tile mean motion values in [0..1] from a diff image (uint8).

    Output ordering:
    - Row-major (r0c0, r0c1, ..., r1c0, ...)

    Behavior:
    - Empty tiles (possible if edges collapse due to extreme sizing) return 0.0.
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
            out.append(float(tile.mean() / 255.0) if tile.size else 0.0)
    return out


def _topk_mean(values: List[float], k: int) -> float:
    """
    Average of the k largest values.

    Used to capture “localized spikes” (e.g., motion in one tile) that might be diluted
    in the global mean.
    """
    if not values:
        return 0.0
    kk = max(1, min(int(k), len(values)))
    s = sorted(values, reverse=True)[:kk]
    return float(sum(s) / float(len(s)))


def _detect_dead_top_rows(diff_u8: np.ndarray, *, rows: int, max_rows: int = 5) -> Tuple[int, float, float, float]:
    """
    Heuristic to detect “dead” (all-zero) horizontal bands at the top of the diff image.

    Motivation:
    - Some capture scenarios can introduce an always-zero strip (e.g., stale/blank pixels),
      which can distort tile statistics.
    - If the first N bands are consistently 0.0 mean, we crop them away before computing tiles.

    Returns:
    - dead: number of leading bands with mean == 0.0 (limited by max_rows and rows-1)
    - b1/b2/b3: mean values of first three bands (debug/telemetry potential)
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
    Crop an inset ROI from a grayscale image.

    Why:
    - Borders of windows/overlays often contain high-contrast edges that flicker (anti-aliasing,
      subpixel snapping) and cause false positives.
    - Removing `inset_px` pixels from each side reduces noise.

    Returns:
    - roi: the (possibly) cropped array view
    - rect: metadata describing ROI within the original image (x,y,width,height)

    Safety:
    - If inset removes the entire image, we return the original image to avoid invalid shapes.
    """
    inset = max(0, int(inset_px))
    h, w = gray.shape

    if inset == 0:
        return gray, {"x": 0, "y": 0, "width": int(w), "height": int(h)}

    x0 = min(w, inset)
    y0 = min(h, inset)
    x1 = max(x0, w - inset)
    y1 = max(y0, h - inset)

    if x1 - x0 < 1 or y1 - y0 < 1:
        return gray, {"x": 0, "y": 0, "width": int(w), "height": int(h)}

    roi = gray[y0:y1, x0:x1]
    return roi, {"x": int(x0), "y": int(y0), "width": int(x1 - x0), "height": int(y1 - y0)}


class MonitorLoop:
    """
    Background worker that:
    1) Captures frames for the current region
    2) Computes diff-based motion metrics (per-tile + aggregate)
    3) Updates StatusStore with a JSON-friendly payload
    4) Feeds ClipRecorder when recording is enabled

    Threading model:
    - `start()` spawns a daemon thread that runs until `stop()` is requested.
    - All state needed for motion detection (_prev_gray, EMA) is confined to that thread.
    """

    def __init__(
        self,
        *,
        store: StatusStore,
        capturer: ScreenCapturer,
        params: DetectionParams,
        get_region: Callable[[], Region],
    ) -> None:
        # External collaborators.
        self._store = store
        self._capturer = capturer
        self._params = params
        self._get_region = get_region

        # Thread controls.
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Motion state (owned by the monitor thread).
        self._prev_gray: Optional[np.ndarray] = None
        self._ema_activity: float = 0.0
        self._last_update_ts: float = 0.0
        self._prev_state: str = "ERROR"

        # Recording is always constructed; `enabled` controls actual writes.
        # This keeps runtime logic simple and ensures directory creation happens once.
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
        Start the monitor thread if it is not already running.

        Idempotent:
        - Multiple calls are safe; if the thread is alive we do nothing.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="motiondetector-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to stop (non-blocking)."""
        self._stop.set()

    def join(self, timeout: float) -> None:
        """Join the thread for up to `timeout` seconds."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        """
        Main loop.

        Timing:
        - Attempts to maintain `params.fps` cadence by subtracting processing time from the period.
        - Uses Event.wait(timeout=...) so stop requests interrupt sleep promptly.
        """
        period = 1.0 / max(float(self._params.fps), 1.0)

        try:
            while not self._stop.is_set():
                t0 = time.time()
                region = self._get_region()

                # Capture failures should not crash the loop; publish an error payload and retry.
                try:
                    frame = self._capturer.grab(region)
                except Exception as e:
                    self._store.set_latest(self._error_payload(reason=str(e), region=region))
                    self._stop.wait(timeout=0.05)
                    continue

                # Processing failures should also be isolated. The loop continues with the next frame.
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
            # Always attempt a clean shutdown of optional resources.
            # Failures here should not propagate.
            try:
                self._recorder.stop()
            except Exception:
                pass
            try:
                # Optional API: some capturers keep thread-local handles that should be released.
                self._capturer.close_thread_resources()
            except Exception:
                pass

    def _get_disabled_tiles(self, *, n_tiles: int) -> List[int]:
        """
        Retrieve disabled tiles from StatusStore if that feature exists.

        Rationale:
        - UI/clients may toggle tiles off (masking out areas that are noisy or irrelevant).
        - The store owns that user-driven state; the monitor loop consumes it.

        Defensive behavior:
        - If the store doesn't implement the hook or returns invalid data, we treat as no disabled tiles.
        """
        getter = getattr(self._store, "get_disabled_tiles", None)
        if not callable(getter):
            return []
        try:
            raw = getter()
        except Exception:
            return []

        out: List[int] = []
        if isinstance(raw, list):
            for v in raw:
                if isinstance(v, int) and 0 <= v < n_tiles:
                    out.append(v)
        return sorted(set(out))

    def _process_frame(self, *, frame: np.ndarray, ts: float, region: Region) -> Dict:
        """
        Convert a captured frame into a JSON payload.

        Pipeline:
        1) Convert to grayscale
        2) Apply inset crop to reduce border noise
        3) Warm-up: if no previous frame, publish a “warming_up” payload
        4) Compute absolute diff vs previous frame
        5) Optional dead-row cropping heuristic
        6) Compute per-tile means and normalized activity
        7) Apply tile masking (disabled tiles => None in JSON)
        8) Update EMA and classify into a discrete state
        9) Optionally feed the recorder
        """
        gray_full = _to_gray_u8(frame)

        rows = int(self._params.grid_rows)
        cols = int(self._params.grid_cols)
        n_tiles = rows * cols

        # Inset is applied before diffing and tiling so the entire metric stream is consistent.
        gray, _inset_rect = _apply_inset(gray_full, int(getattr(self._params, "analysis_inset_px", 10)))

        disabled_tiles = self._get_disabled_tiles(n_tiles=n_tiles)
        disabled_set: Set[int] = set(disabled_tiles)

        # Warm-up state: no diff can be computed yet, or the region size changed (e.g., user resized overlay).
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            self._last_update_ts = ts
            self._prev_state = "ERROR"

            # Emit tiles as 0.0 for enabled and None for disabled so the client can render immediately.
            tiles_init: List[Optional[float]] = [0.0] * n_tiles
            for i in disabled_set:
                tiles_init[i] = None

            return self._status_payload(
                ts=ts,
                region=region,
                video_state="ERROR",
                confidence=0.0,
                motion_mean=0.0,
                tiles=tiles_init,
                disabled_tiles=disabled_tiles,
                rows=rows,
                cols=cols,
                overall_state="NOT_OK",
                overall_reasons=["warming_up"],
                errors=["warming_up"],
                stale=False,
                stale_age_sec=0.0,
            )

        # Absolute per-pixel difference between consecutive frames.
        # int16 avoids underflow during subtraction; convert back to u8 for compact stats.
        diff = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16)).astype(np.uint8)
        self._prev_gray = gray
        self._last_update_ts = ts

        # Detect and crop away dead top bands if present to prevent skewing tile stats.
        dead_rows, _b1, _b2, _b3 = _detect_dead_top_rows(diff, rows=rows, max_rows=5)

        if dead_rows > 0:
            tile_h = max(1, diff.shape[0] // max(rows, 1))
            crop_top = min(diff.shape[0] - 1, dead_rows * tile_h)
            diff_roi = diff[crop_top:, :]
        else:
            diff_roi = diff

        # Per-tile metrics come from ROI, but the grid dimensions remain the configured rows/cols.
        tiles_raw = _tile_means(diff_roi, rows=rows, cols=cols)

        # Global mean motion, scaled by diff_gain and capped at 1.0 before normalization.
        mean_raw = float(diff_roi.mean() / 255.0)
        mean_raw = float(min(1.0, mean_raw * float(self._params.diff_gain)))

        # Validate normalization parameters (configuration errors should be surfaced clearly).
        mean_full = float(self._params.mean_full_scale)
        tile_full = float(self._params.tile_full_scale)
        if mean_full <= 0.0:
            raise ValueError("mean_full_scale must be > 0")
        if tile_full <= 0.0:
            raise ValueError("tile_full_scale must be > 0")

        # Normalize into [0..1] using linear “full scale” targets.
        mean_norm_unmasked = _clamp01(mean_raw / mean_full)
        tiles_norm_full: List[float] = [_clamp01(t / tile_full) for t in tiles_raw]

        # Apply tile masking for the state decision.
        enabled_tiles_norm: List[float] = [v for i, v in enumerate(tiles_norm_full) if i not in disabled_set]
        enabled_count = len(enabled_tiles_norm)

        all_tiles_disabled = enabled_count == 0

        if all_tiles_disabled:
            # With no active tiles, motion classification is meaningless; report a stable state.
            self._ema_activity = 0.0
            video_state = "ALL_TILES_DISABLED"
            confidence = 0.0
            overall_state = "OK"
            overall_reasons: List[str] = ["all_tiles_disabled"]
        else:
            # Aggregate activity:
            # - mean over enabled tiles captures distributed motion
            # - top-k captures localized motion (k=1 is “hottest tile”)
            mean_norm = float(sum(enabled_tiles_norm) / float(enabled_count))
            topk_mean = _topk_mean(enabled_tiles_norm, 1)
            activity = max(mean_norm, topk_mean)

            # EMA smoothing reduces flicker and helps thresholds behave consistently.
            a = float(self._params.ema_alpha)
            self._ema_activity = (a * activity) + ((1.0 - a) * self._ema_activity)

            # Convert smoothed activity into discrete states.
            if self._ema_activity < float(self._params.no_motion_threshold):
                video_state = "NO_MOTION"
            elif self._ema_activity < float(self._params.low_activity_threshold):
                video_state = "LOW_ACTIVITY"
            else:
                video_state = "MOTION"

            confidence = _clamp01(self._ema_activity)

            # Overall status is a higher-level “OK/NOT_OK” signal for simple consumers.
            # Current rule: only MOTION is OK, otherwise NOT_OK.
            overall_state = "OK" if video_state == "MOTION" else "NOT_OK"
            overall_reasons = [] if video_state == "MOTION" else ["no_motion_enabled_tiles"]

        # JSON tiles are always full grid length; disabled tiles become null.
        tiles_for_json: List[Optional[float]] = [float(v) for v in tiles_norm_full]
        for i in disabled_set:
            tiles_for_json[i] = None

        payload = self._status_payload(
            ts=ts,
            region=region,
            video_state=video_state,
            confidence=confidence,
            motion_mean=float(self._ema_activity),
            tiles=tiles_for_json,
            disabled_tiles=disabled_tiles,
            rows=rows,
            cols=cols,
            overall_state=overall_state,
            overall_reasons=overall_reasons,
            errors=[],
            stale=False,
            stale_age_sec=0.0,
        )

        # Recording is only meaningful when there is at least one enabled tile and we have a “real” state.
        if not all_tiles_disabled:
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
        tiles: List[Optional[float]],
        disabled_tiles: List[int],
        rows: int,
        cols: int,
        overall_state: str,
        overall_reasons: List[str],
        errors: List[str],
        stale: bool,
        stale_age_sec: float,
    ) -> Dict:
        """
        Build the canonical JSON payload.

        Notes:
        - `tiles` may contain None for disabled tiles; we normalize floats for JSON stability.
        - `capture.backend` is hardcoded to "MSS" because ScreenCapturer currently only supports MSS.
          If you add more backends, thread this through from ScreenCapturer/params.
        """
        tiles_list: List[Optional[float]] = [float(x) if x is not None else None for x in tiles]

        return {
            "timestamp": float(ts),
            "capture": {"state": "OK", "reason": "ok", "backend": "MSS"},
            "video": {
                "state": str(video_state),
                "confidence": float(confidence),
                "motion_mean": float(motion_mean),
                "grid": {"rows": int(rows), "cols": int(cols)},
                "tiles": tiles_list,
                "disabled_tiles": [int(i) for i in disabled_tiles],
                "stale": bool(stale),
                "stale_age_sec": float(stale_age_sec),
            },
            "overall": {"state": str(overall_state), "reasons": list(overall_reasons)},
            "errors": list(errors),
            "region": {"x": int(region.x), "y": int(region.y), "width": int(region.width), "height": int(region.height)},
        }

    @staticmethod
    def _error_payload(*, reason: str, region: Region) -> Dict:
        """
        Build an error payload (capture or processing failure).

        Conventions:
        - `capture.state` is ERROR with a human-readable reason.
        - `video.stale` is True (data cannot be trusted).
        - `overall.state` is NOT_OK.
        """
        now = time.time()
        return {
            "timestamp": float(now),
            "capture": {"state": "ERROR", "reason": str(reason), "backend": "MSS"},
            "video": {
                "state": "ERROR",
                "confidence": 0.0,
                "motion_mean": 0.0,
                "grid": {"rows": 0, "cols": 0},
                "tiles": [],
                "disabled_tiles": [],
                "stale": True,
                "stale_age_sec": 0.0,
            },
            "overall": {"state": "NOT_OK", "reasons": ["capture_error"]},
            "errors": [str(reason)],
            "region": {"x": int(region.x), "y": int(region.y), "width": int(region.width), "height": int(region.height)},
        }
