# File commentary: analyzer/monitor_loop.py - This file holds logic used by the motion detector project.
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
    motion_instant_mean, motion_instant_top1, motion_instant_activity,
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
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple, Set

import cv2
import numpy as np

from analyzer.capture import Region, ScreenCapturer
from analyzer.audio_meter import AudioMeter
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
    no_motion_grace_period_seconds: float
    no_motion_grace_required_ratio: float
    ema_alpha: float

    mean_full_scale: float
    tile_full_scale: float

    grid_rows: int
    grid_cols: int

    blockiness_enabled: bool
    blockiness_block_sizes: List[int]
    blockiness_sample_every_frames: int
    blockiness_downscale_width: int
    blockiness_ema_alpha: float

    record_enabled: bool
    record_trigger_state: str
    record_clip_seconds: int
    record_cooldown_seconds: int
    record_assets_dir: str
    record_stop_grace_seconds: int = 10

    analysis_inset_px: int = 10

    audio_enabled: bool = True
    audio_backend: str = "pyaudiowpatch"
    audio_device_substr: str = ""
    audio_device_index: Optional[int] = None
    audio_device_id: str = ""
    audio_samplerate: int = 48_000
    audio_channels: int = 2
    audio_block_ms: int = 250
    audio_calib_sec: float = 2.0
    audio_factor: float = 2.5
    audio_abs_min: float = 0.00012
    audio_process_names: str = ""
    audio_on_threshold: float = 0.01
    audio_off_threshold: float = 0.005
    audio_hold_ms: int = 300
    audio_smooth_samples: int = 3



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
    """Average of the k largest values."""
    if not values:
        return 0.0
    kk = max(1, min(int(k), len(values)))
    s = sorted(values, reverse=True)[:kk]
    return float(sum(s) / float(len(s)))


def _detect_dead_top_rows(diff_u8: np.ndarray, *, rows: int, max_rows: int = 5) -> Tuple[int, float, float, float]:
    """
    Heuristic to detect “dead” (all-zero) horizontal bands at the top of the diff image.
    """
    h = int(diff_u8.shape[0])
    tile_h = max(1, h // max(rows, 1))

    def band_mean(i: int) -> float:
        """Handle band mean for this module."""
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




def _downscale_gray(gray: np.ndarray, downscale_width: int) -> np.ndarray:
    """Downscale grayscale image to target width while preserving aspect ratio."""
    h, w = gray.shape
    target_w = max(1, int(downscale_width))
    if w <= target_w:
        return gray
    target_h = max(1, int(round((h * target_w) / float(w))))
    return cv2.resize(gray, (target_w, target_h), interpolation=cv2.INTER_AREA)


def _gradient_mean_at_positions(gray: np.ndarray, positions: np.ndarray, axis: int) -> float:
    """Mean absolute pixel gradient over edge positions along x (axis=1) or y (axis=0)."""
    if positions.size == 0:
        return 0.0
    arr = gray.astype(np.int16)
    if axis == 1:
        diffs = np.abs(arr[:, positions] - arr[:, positions - 1])
    else:
        diffs = np.abs(arr[positions, :] - arr[positions - 1, :])
    return float(diffs.mean()) if diffs.size else 0.0


def _blockiness_score_for_size(gray: np.ndarray, block_size: int, eps: float = 1e-6) -> Optional[float]:
    """Compute no-reference blockiness score for one block size."""
    h, w = gray.shape
    b = max(2, int(block_size))
    if h < 2 or w < 2:
        return None

    x_all = np.arange(1, w, dtype=np.int32)
    y_all = np.arange(1, h, dtype=np.int32)
    x_bound = x_all[(x_all % b) == 0]
    y_bound = y_all[(y_all % b) == 0]
    if x_bound.size == 0 and y_bound.size == 0:
        return None

    x_dist = np.minimum(x_all % b, b - (x_all % b))
    y_dist = np.minimum(y_all % b, b - (y_all % b))
    x_int = x_all[x_dist > 1]
    y_int = y_all[y_dist > 1]

    gv_bound = _gradient_mean_at_positions(gray, x_bound, axis=1)
    gh_bound = _gradient_mean_at_positions(gray, y_bound, axis=0)
    gv_int = _gradient_mean_at_positions(gray, x_int, axis=1)
    gh_int = _gradient_mean_at_positions(gray, y_int, axis=0)

    bound = 0.5 * (gv_bound + gh_bound)
    interior = 0.5 * (gv_int + gh_int)
    score = bound / (interior + float(eps))
    return float(max(0.0, score))

def _confidence_from_thresholds(*, ema_activity: float, no_thr: float, low_thr: float) -> float:
    """
    A simple, monotonic confidence estimate in [0..1] based on distance from thresholds.

    Interpretation:
    - NO_MOTION: confidence grows as ema goes further below no_thr
    - LOW_ACTIVITY: confidence grows as ema sits further away from either boundary
    - MOTION: confidence grows as ema goes further above low_thr
    """
    e = float(ema_activity)
    no_t = float(no_thr)
    low_t = float(low_thr)

    if no_t <= 0.0:
        return 0.0
    if low_t <= no_t:
        return 0.0

    if e < no_t:
        # 1.0 at 0, 0.0 at no_thr
        return _clamp01((no_t - e) / no_t)

    if e < low_t:
        # Peak in the middle of the band, 0.0 at boundaries
        mid = 0.5 * (no_t + low_t)
        half = 0.5 * (low_t - no_t)
        if half <= 0.0:
            return 0.0
        return _clamp01(1.0 - (abs(e - mid) / half))

    # MOTION: ramp from low_thr upward
    denom = max(1e-9, 1.0 - low_t)
    return _clamp01((e - low_t) / denom)


class MonitorLoop:
    """
    Background worker that:
    1) Captures frames for the current region
    2) Computes diff-based motion metrics (per-tile + aggregate)
    3) Updates StatusStore with a JSON-friendly payload
    4) Feeds ClipRecorder when recording is enabled
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

        self._prev_gray: Optional[np.ndarray] = None
        self._ema_activity: float = 0.0
        self._last_update_ts: float = 0.0
        self._prev_state: str = "ERROR"
        self._no_motion_votes: Deque[Tuple[float, bool]] = deque()

        block_sizes = list(getattr(self._params, "blockiness_block_sizes", [8, 16]) or [8, 16])
        self._blockiness_block_sizes: List[int] = sorted(set(max(2, int(v)) for v in block_sizes))
        self._blockiness_frame_index: int = 0
        self._blockiness_score: Optional[float] = None
        self._blockiness_score_ema: Optional[float] = None
        self._blockiness_score_by_block: Dict[str, Optional[float]] = {str(b): None for b in self._blockiness_block_sizes}

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
        process_names = [p.strip() for p in str(params.audio_process_names).split(",") if p.strip()]
        self._audio = AudioMeter(
            enabled=bool(params.audio_enabled),
            backend=str(params.audio_backend),
            device_substr=str(params.audio_device_substr),
            device_index=params.audio_device_index,
            device_id=str(params.audio_device_id),
            samplerate=int(params.audio_samplerate),
            channels=int(params.audio_channels),
            block_ms=int(params.audio_block_ms),
            process_names=process_names if process_names else None,
            on_threshold=float(params.audio_on_threshold),
            off_threshold=float(params.audio_off_threshold),
            hold_ms=int(params.audio_hold_ms),
            smooth_samples=int(params.audio_smooth_samples),
        )

    def start(self) -> None:
        """Start background work for this component so it can begin producing updates."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._audio.start()
        self._thread = threading.Thread(target=self._run, name="motiondetector-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Request a clean shutdown for this component and stop ongoing background work."""
        self._stop.set()
        self._audio.stop()

    def join(self, timeout: float) -> None:
        """Wait for the background worker to finish so shutdown is complete."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        """Execute the main loop for this component until shutdown is requested."""
        period = 1.0 / max(float(self._params.fps), 1.0)
        try:
            while not self._stop.is_set():
                t0 = time.time()
                region = self._get_region()

                try:
                    frame = self._capturer.grab(region)
                except Exception as e:
                    self._store.set_latest(self._error_payload(reason=str(e), region=region, audio=self._audio.get_level()))
                    self._stop.wait(timeout=0.05)
                    continue

                try:
                    payload = self._process_frame(frame=frame, ts=t0, region=region)
                    self._store.set_latest(payload)
                except Exception as e:
                    self._store.set_latest(self._error_payload(reason=f"process_failed: {e}", region=region, audio=self._audio.get_level()))

                elapsed = time.time() - t0
                sleep_for = period - elapsed
                if sleep_for > 0:
                    self._stop.wait(timeout=sleep_for)
        finally:
            try:
                self._recorder.stop()
            except Exception:
                pass
            try:
                self._capturer.close_thread_resources()
            except Exception:
                pass
            try:
                self._audio.stop()
            except Exception:
                pass

    def _get_disabled_tiles(self, *, n_tiles: int) -> List[int]:
        """Return the current disabled tiles value for callers."""
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

    def _resolve_video_state_with_grace(self, *, ts: float, no_motion_candidate: bool) -> str:
        """Resolve video state with grace for this module's workflow."""
        grace_period = max(0.0, float(getattr(self._params, "no_motion_grace_period_seconds", 0.0)))
        required_ratio = float(getattr(self._params, "no_motion_grace_required_ratio", 1.0))
        required_ratio = _clamp01(required_ratio)

        if grace_period <= 0.0:
            return "NO_MOTION" if no_motion_candidate else "MOTION_OR_LOW"

        self._no_motion_votes.append((float(ts), bool(no_motion_candidate)))
        cutoff = float(ts) - grace_period
        while self._no_motion_votes and self._no_motion_votes[0][0] < cutoff:
            self._no_motion_votes.popleft()

        window_count = len(self._no_motion_votes)
        if window_count == 0:
            return "MOTION_OR_LOW"

        no_motion_count = sum(1 for _, vote in self._no_motion_votes if vote)
        if (float(no_motion_count) / float(window_count)) >= required_ratio:
            return "NO_MOTION"
        return "MOTION_OR_LOW"

    def _maybe_update_blockiness(self, gray: np.ndarray) -> None:
        """Sample blockiness every N frames and keep last value between samples."""
        self._blockiness_frame_index += 1
        if not bool(getattr(self._params, "blockiness_enabled", True)):
            return

        every = max(1, int(getattr(self._params, "blockiness_sample_every_frames", 25)))
        if (self._blockiness_frame_index % every) != 0:
            return

        scaled = _downscale_gray(gray, int(getattr(self._params, "blockiness_downscale_width", 640)))
        score_by_block: Dict[str, Optional[float]] = {}
        scores: List[float] = []
        for b in self._blockiness_block_sizes:
            score_b = _blockiness_score_for_size(scaled, b)
            score_by_block[str(b)] = None if score_b is None else float(score_b)
            if score_b is not None:
                scores.append(float(score_b))

        self._blockiness_score_by_block = score_by_block
        self._blockiness_score = max(scores) if scores else None
        if self._blockiness_score is not None:
            a = float(getattr(self._params, "blockiness_ema_alpha", 0.25))
            if self._blockiness_score_ema is None:
                self._blockiness_score_ema = float(self._blockiness_score)
            else:
                self._blockiness_score_ema = (a * float(self._blockiness_score)) + ((1.0 - a) * float(self._blockiness_score_ema))

    def _blockiness_payload(self) -> Dict:
        """Expose blockiness data in a stable JSON shape."""
        enabled = bool(getattr(self._params, "blockiness_enabled", True))
        return {
            "enabled": enabled,
            "block_sizes": list(self._blockiness_block_sizes),
            "score": float(self._blockiness_score) if (enabled and self._blockiness_score is not None) else None,
            "score_ema": float(self._blockiness_score_ema) if (enabled and self._blockiness_score_ema is not None) else None,
            "score_by_block": {
                str(b): (
                    float(self._blockiness_score_by_block.get(str(b)))
                    if (enabled and self._blockiness_score_by_block.get(str(b)) is not None)
                    else None
                )
                for b in self._blockiness_block_sizes
            },
            "sample_every_frames": int(getattr(self._params, "blockiness_sample_every_frames", 25)),
            "downscale_width": int(getattr(self._params, "blockiness_downscale_width", 640)),
        }

    def _process_frame(self, *, frame: np.ndarray, ts: float, region: Region) -> Dict:
        """Process frame for this module's workflow."""
        gray_full = _to_gray_u8(frame)
        audio = self._audio.get_level()

        rows, cols = self._store.get_grid()
        n_tiles = rows * cols

        gray, _inset_rect = _apply_inset(gray_full, int(getattr(self._params, "analysis_inset_px", 10)))
        self._maybe_update_blockiness(gray)

        disabled_tiles = self._get_disabled_tiles(n_tiles=n_tiles)
        disabled_set: Set[int] = set(disabled_tiles)

        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            self._last_update_ts = ts
            self._prev_state = "ERROR"
            self._ema_activity = 0.0
            self._no_motion_votes.clear()

            tiles_init: List[Optional[float]] = [0.0] * n_tiles
            for i in disabled_set:
                tiles_init[i] = None

            return self._status_payload(
                ts=ts,
                region=region,
                video_state="ERROR",
                confidence=0.0,
                motion_mean=0.0,
                motion_instant_mean=0.0,
                motion_instant_top1=0.0,
                motion_instant_activity=0.0,
                tiles=tiles_init,
                disabled_tiles=disabled_tiles,
                rows=rows,
                cols=cols,
                overall_state="NOT_OK",
                overall_reasons=["warming_up"],
                errors=["warming_up"],
                stale=False,
                stale_age_sec=0.0,
                audio=audio,
                blockiness=self._blockiness_payload(),
            )

        diff = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16)).astype(np.uint8)
        self._prev_gray = gray
        self._last_update_ts = ts

        dead_rows, _b1, _b2, _b3 = _detect_dead_top_rows(diff, rows=rows, max_rows=5)

        if dead_rows > 0:
            tile_h = max(1, diff.shape[0] // max(rows, 1))
            crop_top = min(diff.shape[0] - 1, dead_rows * tile_h)
            diff_roi = diff[crop_top:, :]
        else:
            diff_roi = diff

        tiles_raw = _tile_means(diff_roi, rows=rows, cols=cols)

        mean_raw = float(diff_roi.mean() / 255.0)
        mean_raw = float(min(1.0, mean_raw * float(self._params.diff_gain)))

        mean_full = float(self._params.mean_full_scale)
        tile_full = float(self._params.tile_full_scale)
        if mean_full <= 0.0:
            raise ValueError("mean_full_scale must be > 0")
        if tile_full <= 0.0:
            raise ValueError("tile_full_scale must be > 0")

        mean_norm_unmasked = _clamp01(mean_raw / mean_full)
        tiles_norm_full: List[float] = [_clamp01(t / tile_full) for t in tiles_raw]

        enabled_tiles_norm: List[float] = [v for i, v in enumerate(tiles_norm_full) if i not in disabled_set]
        enabled_count = len(enabled_tiles_norm)
        all_tiles_disabled = enabled_count == 0

        audio = self._audio.get_level()

        if all_tiles_disabled:
            self._ema_activity = 0.0
            self._no_motion_votes.clear()
            video_state = "ALL_TILES_DISABLED"
            confidence = 0.0
            overall_state = "OK"
            overall_reasons: List[str] = ["all_tiles_disabled"]
            motion_instant_mean = 0.0
            motion_instant_top1 = 0.0
            motion_instant_activity = 0.0
            video_state = "ALL_TILES_DISABLED"
        else:
            motion_instant_mean = float(sum(enabled_tiles_norm) / float(enabled_count))
            motion_instant_top1 = _topk_mean(enabled_tiles_norm, 1)
            motion_instant_activity = motion_instant_activity = _topk_mean(enabled_tiles_norm, k=3)

            a = float(self._params.ema_alpha)
            self._ema_activity = (a * motion_instant_activity) + ((1.0 - a) * self._ema_activity)

            # Use the instantaneous top tile for NO_MOTION candidacy so the signal can
            # drop quickly when movement stops, while still keeping EMA for MOTION/
            # LOW_ACTIVITY stability and confidence reporting.
            no_motion_candidate = motion_instant_top1 < float(self._params.no_motion_threshold)
            state_with_grace = self._resolve_video_state_with_grace(
                ts=ts,
                no_motion_candidate=no_motion_candidate,
            )
            if state_with_grace == "NO_MOTION":
                motion_state = "NO_MOTION"
            elif self._ema_activity < float(self._params.low_activity_threshold):
                motion_state = "LOW_ACTIVITY"
            else:
                motion_state = "MOTION"

            confidence = _confidence_from_thresholds(
                ema_activity=self._ema_activity,
                no_thr=float(self._params.no_motion_threshold),
                low_thr=float(self._params.low_activity_threshold),
            )

            overall_state = "OK" if motion_state == "MOTION" else "NOT_OK"
            overall_reasons = [] if motion_state == "MOTION" else ["no_motion_enabled_tiles"]

            if audio.available:
                has_audio = bool(getattr(audio, "detected", max(audio.left, audio.right) > 1.0))
                suffix = "WITH_AUDIO" if has_audio else "NO_AUDIO"
                video_state = f"{motion_state}_{suffix}"
            else:
                video_state = f"{motion_state}_NOSOUNDHARDWARE"

        tiles_for_json: List[Optional[float]] = [float(v) for v in tiles_norm_full]
        for i in disabled_set:
            tiles_for_json[i] = None

        payload = self._status_payload(
            ts=ts,
            region=region,
            video_state=video_state,
            confidence=confidence,
            motion_mean=float(self._ema_activity),
            motion_instant_mean=float(motion_instant_mean),
            motion_instant_top1=float(motion_instant_top1),
            motion_instant_activity=float(motion_instant_activity),
            tiles=tiles_for_json,
            disabled_tiles=disabled_tiles,
            rows=rows,
            cols=cols,
            overall_state=overall_state,
            overall_reasons=overall_reasons,
            errors=[],
            stale=False,
            stale_age_sec=0.0,
            audio=audio,
            blockiness=self._blockiness_payload(),
        )

        if not all_tiles_disabled:
            frame_bgr = _bgra_to_bgr(frame)
            try:
                self._recorder.update(now_ts=ts, state=video_state, frame_bgr=frame_bgr)
            except Exception as e:
                # Recording failures should not break capture/detection payloads.
                payload_errors = payload.get("errors")
                if isinstance(payload_errors, list):
                    payload_errors.append(f"recorder_failed: {e}")

        self._prev_state = video_state
        _ = mean_norm_unmasked  # retained for future telemetry if needed
        return payload

    @staticmethod
    def _status_payload(
        *,
        ts: float,
        region: Region,
        video_state: str,
        confidence: float,
        motion_mean: float,
        motion_instant_mean: float,
        motion_instant_top1: float,
        motion_instant_activity: float,
        tiles: List[Optional[float]],
        disabled_tiles: List[int],
        rows: int,
        cols: int,
        overall_state: str,
        overall_reasons: List[str],
        errors: List[str],
        stale: bool,
        stale_age_sec: float,
        audio,
        blockiness: Optional[Dict[str, object]] = None,
    ) -> Dict:
        tiles_list: List[Optional[float]] = [float(x) if x is not None else None for x in tiles]
        tiles_indexed = [
            {"tile": i, "value": "disabled" if v is None else float(v)}
            for i, v in enumerate(tiles_list)
        ]
        disabled_tiles_list: List[int] = [i for i, v in enumerate(tiles_list) if v is None]

        return {
            "timestamp": float(ts),
            "capture": {"state": "OK", "reason": "ok", "backend": "MSS"},
            "video": {
                "state": str(video_state),
                "confidence": float(confidence),
                "motion_mean": float(motion_mean),
                "motion_instant_mean": float(motion_instant_mean),
                "motion_instant_top1": float(motion_instant_top1),
                "motion_instant_activity": float(motion_instant_activity),
                "grid": {"rows": int(rows), "cols": int(cols)},
                "tiles": tiles_list,
                "tiles_indexed": tiles_indexed,
                "disabled_tiles": disabled_tiles_list,
                "stale": bool(stale),
                "stale_age_sec": float(stale_age_sec),
                "blockiness": dict(blockiness) if isinstance(blockiness, dict) else {
                    "enabled": False,
                    "block_sizes": [8, 16],
                    "score": None,
                    "score_ema": None,
                    "score_by_block": {"8": None, "16": None},
                    "sample_every_frames": 25,
                    "downscale_width": 640,
                },
            },
            "audio": {
                "available": bool(audio.available),
                "left": float(audio.left),
                "right": float(audio.right),
                "detected": bool(getattr(audio, "detected", False)),
                "reason": str(audio.reason),
            },
            "overall": {"state": str(overall_state), "reasons": list(overall_reasons)},
            "errors": list(errors),
            "region": {"x": int(region.x), "y": int(region.y), "width": int(region.width), "height": int(region.height)},
        }

    @staticmethod
    def _error_payload(*, reason: str, region: Region, audio=None) -> Dict:
        """Handle error payload for this module."""
        now = time.time()
        audio_available = bool(getattr(audio, "available", False))
        audio_left = float(getattr(audio, "left", 0.0))
        audio_right = float(getattr(audio, "right", 0.0))
        audio_detected = bool(getattr(audio, "detected", False))
        audio_reason = str(getattr(audio, "reason", "unavailable"))
        return {
            "timestamp": float(now),
            "capture": {"state": "ERROR", "reason": str(reason), "backend": "MSS"},
            "video": {
                "state": "ERROR",
                "confidence": 0.0,
                "motion_mean": 0.0,
                "motion_instant_mean": 0.0,
                "motion_instant_top1": 0.0,
                "motion_instant_activity": 0.0,
                "grid": {"rows": 0, "cols": 0},
                "tiles": [],
                "tiles_indexed": [],
                "disabled_tiles": [],
                "stale": True,
                "stale_age_sec": 0.0,
                "blockiness": {
                    "enabled": False,
                    "block_sizes": [8, 16],
                    "score": None,
                    "score_ema": None,
                    "score_by_block": {"8": None, "16": None},
                    "sample_every_frames": 25,
                    "downscale_width": 640,
                },
            },
            "audio": {
                "available": audio_available,
                "left": audio_left,
                "right": audio_right,
                "detected": audio_detected,
                "reason": audio_reason,
            },
            "overall": {"state": "NOT_OK", "reasons": ["capture_error"]},
            "errors": [str(reason)],
            "region": {"x": int(region.x), "y": int(region.y), "width": int(region.width), "height": int(region.height)},
        }
