# analyzer/monitor_loop.py
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
    record_trigger_state: str  # "NO_MOTION"
    record_clip_seconds: int
    record_cooldown_seconds: int
    record_assets_dir: str
    record_stop_grace_seconds: int = 10  # NEW

    # NEW: inset inside the captured region before computing diff/tiles
    analysis_inset_px: int = 10


def _to_gray_u8(frame_bgra: np.ndarray) -> np.ndarray:
    if frame_bgra.ndim != 3 or frame_bgra.shape[2] != 4:
        raise ValueError(f"Expected BGRA frame (H,W,4), got {frame_bgra.shape}")
    b = frame_bgra[:, :, 0].astype(np.uint16)
    g = frame_bgra[:, :, 1].astype(np.uint16)
    r = frame_bgra[:, :, 2].astype(np.uint16)
    y = (77 * r + 150 * g + 29 * b) >> 8
    return y.astype(np.uint8)


def _bgra_to_bgr(frame_bgra: np.ndarray) -> np.ndarray:
    if frame_bgra.ndim != 3 or frame_bgra.shape[2] != 4:
        raise ValueError(f"Expected BGRA frame (H,W,4), got {frame_bgra.shape}")
    return frame_bgra[:, :, :3].copy()


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _edges(size: int, parts: int) -> List[int]:
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
    if not values:
        return 0.0
    kk = max(1, min(int(k), len(values)))
    s = sorted(values, reverse=True)[:kk]
    return float(sum(s) / float(len(s)))


def _tiles_named(tiles: List[float], *, rows: int, cols: int) -> Dict[str, float]:
    n = int(rows) * int(cols)
    if len(tiles) != n:
        raise ValueError(f"tiles length {len(tiles)} != rows*cols {n} (rows={rows}, cols={cols})")
    return {f"t{i + 1}": float(tiles[i]) for i in range(n)}


def _detect_dead_top_rows(diff_u8: np.ndarray, *, rows: int, max_rows: int = 5) -> Tuple[int, float, float, float]:
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
    inset = max(0, int(inset_px))
    if inset == 0:
        h, w = gray.shape
        return gray, {"x": 0, "y": 0, "width": int(w), "height": int(h)}

    h, w = gray.shape
    x0 = min(w, inset)
    y0 = min(h, inset)
    x1 = max(x0, w - inset)
    y1 = max(y0, h - inset)

    if x1 - x0 < 1 or y1 - y0 < 1:
        return gray, {"x": 0, "y": 0, "width": int(w), "height": int(h)}

    roi = gray[y0:y1, x0:x1]
    return roi, {"x": int(x0), "y": int(y0), "width": int(x1 - x0), "height": int(y1 - y0)}


class MonitorLoop:
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
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="motiondetector-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        period = 1.0 / max(float(self._params.fps), 1.0)

        try:
            while not self._stop.is_set():
                t0 = time.time()
                region = self._get_region()

                try:
                    frame = self._capturer.grab(region)
                except Exception as e:
                    self._store.set_latest(self._error_payload(reason=str(e), region=region))
                    self._stop.wait(timeout=0.05)
                    continue

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
            try:
                self._recorder.stop()
            except Exception:
                pass
            try:
                self._capturer.close_thread_resources()
            except Exception:
                pass

    def _process_frame(self, *, frame: np.ndarray, ts: float, region: Region) -> Dict:
        gray_full = _to_gray_u8(frame)

        rows = int(self._params.grid_rows)
        cols = int(self._params.grid_cols)

        gray, inset_rect = _apply_inset(gray_full, int(getattr(self._params, "analysis_inset_px", 10)))

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

        diff = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16)).astype(np.uint8)
        self._prev_gray = gray
        self._last_update_ts = ts

        dead_rows, b1, b2, b3 = _detect_dead_top_rows(diff, rows=rows, max_rows=5)
        print("bands:", b1, b2, b3, "dead_rows:", dead_rows)

        if dead_rows > 0:
            tile_h = max(1, diff.shape[0] // max(rows, 1))
            crop_top = min(diff.shape[0] - 1, dead_rows * tile_h)
            diff_roi = diff[crop_top:, :]
            roi_rect = {"x": 0, "y": int(crop_top), "width": int(diff.shape[1]), "height": int(diff_roi.shape[0])}
        else:
            diff_roi = diff
            roi_rect = {"x": 0, "y": 0, "width": int(diff.shape[1]), "height": int(diff.shape[0])}

        tiles_raw = _tile_means(diff_roi, rows=rows, cols=cols)
        mean_raw = float(diff_roi.mean() / 255.0)

        mean_raw = float(min(1.0, mean_raw * float(self._params.diff_gain)))

        mean_full = float(self._params.mean_full_scale)
        tile_full = float(self._params.tile_full_scale)
        if mean_full <= 0.0:
            raise ValueError("mean_full_scale must be > 0")
        if tile_full <= 0.0:
            raise ValueError("tile_full_scale must be > 0")

        mean_norm = _clamp01(mean_raw / mean_full)
        tiles_norm = [_clamp01(t / tile_full) for t in tiles_raw]

        topk_mean = _topk_mean(tiles_norm, 1)
        activity = max(mean_norm, topk_mean)

        a = float(self._params.ema_alpha)
        self._ema_activity = (a * activity) + ((1.0 - a) * self._ema_activity)

        if self._ema_activity < float(self._params.no_motion_threshold):
            video_state = "NO_MOTION"
        elif self._ema_activity < float(self._params.low_activity_threshold):
            video_state = "LOW_ACTIVITY"
        else:
            video_state = "MOTION"

        confidence = _clamp01(self._ema_activity)

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
