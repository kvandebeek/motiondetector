# ui/testdata_window.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence, Tuple

import httpx
import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QWidget

from testdata.engine import FrameOut, SubtitleOverlay, TestDataEngine
from testdata.logger import TestDataLogger, TestDataLogRow
from testdata.summary import SceneStats, TestDataSummaryWriter


@dataclass(frozen=True)
class TestDataWindowConfig:
    fps: float
    show_overlay_text: bool = True

    # If provided, the window will poll /status for expected-vs-actual evaluation.
    server_base_url: Optional[str] = None  # e.g. http://127.0.0.1:8735
    status_poll_ms: int = 250

    # Logging
    log_dir: str = "./testdata_logs"
    log_every_n_frames: int = 1  # kept for compatibility; logging is per detector sample

    # Display-only metadata
    profile_name: str = "default"


@dataclass(frozen=True)
class DetectorSnapshot:
    status_ts: Optional[float]
    capture_state: Optional[str]

    video_state: Optional[str]
    motion_mean: Optional[float]
    confidence: Optional[float]
    tiles: Optional[Sequence[float]]
    disabled_tiles: Optional[Sequence[int]]

    video_stale: Optional[bool]
    stale_age_sec: Optional[float]


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) else None


def _safe_str(x: Any) -> Optional[str]:
    return str(x) if x is not None else None


def _safe_bool(x: Any) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    return None


def _tiles_stats(tiles: Optional[Sequence[float]]) -> tuple[Optional[float], Optional[float], list[tuple[int, float]]]:
    if tiles is None:
        return None, None, []
    vals: list[float] = []
    for t in tiles:
        if isinstance(t, (int, float)):
            vals.append(float(t))
    if not vals:
        return None, None, []
    tmax = max(vals)
    tmean = sum(vals) / float(len(vals))
    top3 = sorted(list(enumerate(vals)), key=lambda iv: iv[1], reverse=True)[:3]
    return tmax, tmean, [(int(i), float(v)) for i, v in top3]


class TestDataWindow(QWidget):
    def __init__(self, *, engine: TestDataEngine, cfg: TestDataWindowConfig) -> None:
        super().__init__()
        self._engine = engine
        self._cfg = cfg

        self.setWindowTitle(f"motiondetector testdata ({cfg.profile_name})")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self._frame_index: int = 0
        self._last: Optional[FrameOut] = None
        self._qimg: Optional[QImage] = None
        self._buf: Optional[bytes] = None  # keeps QImage backing store alive

        self._det: DetectorSnapshot = DetectorSnapshot(
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        self._last_logged_status_ts: Optional[float] = None

        self._logger = TestDataLogger(log_dir=str(cfg.log_dir))
        self._summary = TestDataSummaryWriter(log_dir=str(cfg.log_dir))

        self._active_stats: Optional[SceneStats] = None
        self._active_key: Optional[Tuple[int, str]] = None

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(max(1, int(round(1000.0 / max(1.0, float(cfg.fps))))))
        self._frame_timer.timeout.connect(self._tick_frame)  # type: ignore[arg-type]
        self._frame_timer.start()

        self._status_timer: Optional[QTimer] = None
        self._http: Optional[httpx.Client] = None
        self._status_url: Optional[str] = None

        if cfg.server_base_url is not None and cfg.server_base_url.strip():
            base = cfg.server_base_url.strip().rstrip("/")
            self._status_url = f"{base}/status"
            self._http = httpx.Client(timeout=0.35)

            self._status_timer = QTimer(self)
            self._status_timer.setInterval(max(100, int(cfg.status_poll_ms)))
            self._status_timer.timeout.connect(self._poll_status)  # type: ignore[arg-type]
            self._status_timer.start()

    # ----------------------------
    # Timers
    # ----------------------------

    def _tick_frame(self) -> None:
        self._frame_index += 1

        self._engine.set_size(w=self.width(), h=self.height())
        self._last = self._engine.next_frame()
        self._qimg = self._to_qimage_rgb(self._last.rgb)

        self.update()

    def _poll_status(self) -> None:
        if self._http is None or self._status_url is None:
            return

        try:
            r = self._http.get(self._status_url)
            r.raise_for_status()
            j = r.json()
            if not isinstance(j, dict):
                return

            status_ts = _safe_float(j.get("timestamp"))

            capture = j.get("capture")
            capture_state: Optional[str] = None
            if isinstance(capture, dict):
                capture_state = _safe_str(capture.get("state"))

            video = j.get("video")
            if not isinstance(video, dict):
                self._det = DetectorSnapshot(status_ts, capture_state, None, None, None, None, None, None, None)
                return

            st = _safe_str(video.get("state"))
            mm = _safe_float(video.get("motion_mean"))
            cf = _safe_float(video.get("confidence"))

            tiles_raw = video.get("tiles")
            tiles: Optional[Sequence[float]]
            if isinstance(tiles_raw, list):
                tiles = [float(t) for t in tiles_raw if isinstance(t, (int, float))]
            else:
                tiles = None

            disabled_raw = video.get("disabled_tiles")
            disabled: Optional[Sequence[int]]
            if isinstance(disabled_raw, list):
                disabled = [int(x) for x in disabled_raw if isinstance(x, (int, float))]
            else:
                disabled = None

            video_stale = _safe_bool(video.get("stale"))
            stale_age_sec = _safe_float(video.get("stale_age_sec"))

            det = DetectorSnapshot(
                status_ts,
                capture_state,
                st,
                mm,
                cf,
                tiles,
                disabled,
                video_stale,
                stale_age_sec,
            )
            self._det = det

            if status_ts is None:
                return
            if self._last_logged_status_ts == status_ts:
                return

            self._last_logged_status_ts = status_ts

            if self._is_valid_detector_sample(det):
                self._update_scene_stats_for_sample()
                self._write_detector_log_row()

        except Exception:
            pass

    def _is_valid_detector_sample(self, det: DetectorSnapshot) -> bool:
        if det.capture_state is not None and det.capture_state != "OK":
            return False
        if det.video_stale is True:
            return False
        if det.video_state is None:
            return False
        if det.motion_mean is None:
            return False
        return True

    # ----------------------------
    # Painting
    # ----------------------------

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        p = QPainter(self)

        if self._qimg is not None:
            p.drawImage(0, 0, self._qimg)

        if self._last is not None and self._last.subtitle is not None:
            self._paint_subtitle(p, self._last.subtitle)

        if self._cfg.show_overlay_text and self._last is not None:
            self._paint_hud(p, self._last, self._det)

    def _paint_subtitle(self, p: QPainter, sub: SubtitleOverlay) -> None:
        p.save()
        p.setOpacity(float(sub.alpha))

        font = QFont()
        font.setPixelSize(max(14, int(self.height() * 0.045)))
        font.setBold(True)
        p.setFont(font)

        p.setPen(QColor(sub.fg_rgb[0], sub.fg_rgb[1], sub.fg_rgb[2]))
        p.drawText(int(sub.x_px), int(sub.y_px), str(sub.text))

        p.restore()

    def _paint_hud(self, p: QPainter, last: FrameOut, det: DetectorSnapshot) -> None:
        font = QFont()
        font.setPixelSize(14)
        font.setBold(True)
        p.setFont(font)

        p.setPen(Qt.GlobalColor.white)
        phase = f"  [{last.phase_name}]" if last.phase_name else ""
        p.drawText(10, 22, f"scene {last.scene_index}: {last.scene_name}{phase}")

        expected = last.expected_state
        actual = det.video_state or "—"
        match = (det.video_state == expected) if det.video_state is not None else None

        if match is True:
            p.setPen(QColor(120, 255, 120))
        elif match is False:
            p.setPen(QColor(255, 120, 120))
        else:
            p.setPen(QColor(255, 220, 120))

        mm_txt = "—" if det.motion_mean is None else f"{det.motion_mean:.6g}"
        cf_txt = "—" if det.confidence is None else f"{det.confidence:.6g}"

        tmax, tmean, top3 = _tiles_stats(det.tiles)
        tmax_txt = "—" if tmax is None else f"{tmax:.6g}"
        tmean_txt = "—" if tmean is None else f"{tmean:.6g}"
        top3_txt = "—" if not top3 else " ".join(f"{i}:{v:.3g}" for i, v in top3)

        stale_txt = "—" if det.video_stale is None else ("1" if det.video_stale else "0")
        ts_txt = "—" if det.status_ts is None else f"{det.status_ts:.3f}"
        cap_txt = det.capture_state or "—"

        p.drawText(
            10,
            42,
            f"expected={expected}  actual={actual}  motion_mean={mm_txt}  conf={cf_txt}  out(ema)={last.ema_activity:.6g}",
        )

        p.setPen(Qt.GlobalColor.white)
        dis_cnt = 0 if det.disabled_tiles is None else len(det.disabled_tiles)
        p.drawText(10, 62, f"tiles: max={tmax_txt} mean={tmean_txt} top3={top3_txt}  disabled={dis_cnt}")

        p.setPen(QColor(200, 200, 200))
        p.drawText(10, 82, f"t={last.scene_time_s:0.1f}s  profile={self._cfg.profile_name}  status_ts={ts_txt}  cap={cap_txt}  stale={stale_txt}")

        p.drawText(10, 102, f"log: {self._logger.path_str}")
        p.drawText(10, 122, f"summary: {self._summary.path_str}")

    # ----------------------------
    # Logging (per detector sample)
    # ----------------------------

    def _write_detector_log_row(self) -> None:
        if self._last is None:
            return

        expected = str(self._last.expected_state)
        actual = self._det.video_state
        match = (actual == expected) if actual is not None else None

        s = getattr(self._engine, "_s", None)
        diff_gain = float(getattr(s, "diff_gain", 0.0))
        ema_alpha = float(getattr(s, "ema_alpha", 0.0))
        no_motion_threshold = float(getattr(s, "no_motion_threshold", 0.0))
        low_activity_threshold = float(getattr(s, "low_activity_threshold", 0.0))
        mean_full_scale = float(getattr(s, "mean_full_scale", 0.0))
        fps = float(getattr(s, "fps", float(self._cfg.fps)))

        row = TestDataLogRow(
            ts_iso=_now_iso_utc(),
            scene_index=int(self._last.scene_index),
            scene_name=str(self._last.scene_name),
            scene_time_s=float(self._last.scene_time_s),
            expected_state=expected,
            output_value=float(self._last.ema_activity),
            detection_value=self._det.motion_mean,
            confidence=self._det.confidence,
            actual_state=actual,
            match=match,
            diff_gain=diff_gain,
            ema_alpha=ema_alpha,
            no_motion_threshold=no_motion_threshold,
            low_activity_threshold=low_activity_threshold,
            mean_full_scale=mean_full_scale,
            fps=fps,
        )
        self._logger.write(row)

    # ----------------------------
    # Summary stats (count per detector sample)
    # ----------------------------

    def _update_scene_stats_for_sample(self) -> None:
        if self._last is None:
            return

        key = (int(self._last.scene_index), str(self._last.phase_name or ""))
        if self._active_key is None:
            self._active_key = key
            self._active_stats = SceneStats(
                scene_index=int(self._last.scene_index),
                scene_name=str(self._last.scene_name),
                phase_name=str(self._last.phase_name or ""),
                expected_state=str(self._last.expected_state),
            )
        elif key != self._active_key:
            if self._active_stats is not None:
                self._summary.write(self._active_stats)

            self._active_key = key
            self._active_stats = SceneStats(
                scene_index=int(self._last.scene_index),
                scene_name=str(self._last.scene_name),
                phase_name=str(self._last.phase_name or ""),
                expected_state=str(self._last.expected_state),
            )

        st = self._active_stats
        if st is None:
            return

        expected = str(self._last.expected_state)
        actual = self._det.video_state

        st.frames += 1

        if actual is not None:
            if actual == expected:
                st.match_frames += 1
            else:
                # LOW_ACTIVITY is motion (same bucket as MOTION) for FP/FN.
                if expected == "NO_MOTION" and actual != "NO_MOTION":
                    st.fp += 1
                if expected in ("LOW_ACTIVITY", "MOTION") and actual == "NO_MOTION":
                    st.fn += 1

        if self._det.motion_mean is not None:
            st.motion_mean_sum += float(self._det.motion_mean)
            st.motion_mean_max = max(float(st.motion_mean_max), float(self._det.motion_mean))

        tmax, _, _ = _tiles_stats(self._det.tiles)
        if tmax is not None:
            st.tile_max_sum += float(tmax)
            st.tile_max_max = max(float(st.tile_max_max), float(tmax))

    # ----------------------------
    # QWidget lifecycle
    # ----------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._frame_timer.stop()
        except Exception:
            pass

        if self._status_timer is not None:
            try:
                self._status_timer.stop()
            except Exception:
                pass

        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass

        try:
            if self._active_stats is not None:
                self._summary.write(self._active_stats)
        except Exception:
            pass

        try:
            self._summary.close()
        except Exception:
            pass

        try:
            self._logger.close()
        except Exception:
            pass

        event.accept()

    # ----------------------------
    # QImage conversion
    # ----------------------------

    def _to_qimage_rgb(self, rgb: np.ndarray) -> QImage:
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("Expected RGB uint8 (H,W,3)")
        h, w = int(rgb.shape[0]), int(rgb.shape[1])
        self._buf = rgb.tobytes(order="C")
        return QImage(self._buf, w, h, 3 * w, QImage.Format.Format_RGB888)
