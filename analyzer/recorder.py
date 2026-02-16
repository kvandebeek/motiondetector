# analyzer/recorder.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class RecorderConfig:
    enabled: bool
    trigger_state: str  # e.g. "NO_MOTION"
    clip_seconds: int
    cooldown_seconds: int
    fps: float
    assets_dir: str
    stop_grace_seconds: int = 10


class ClipRecorder:
    def __init__(self, cfg: RecorderConfig) -> None:
        self._cfg = cfg
        self._writer: Optional[cv2.VideoWriter] = None
        self._frames_left: int = 0
        self._last_start_ts: float = 0.0
        self._stop_deadline_ts: Optional[float] = None

        Path(cfg.assets_dir).mkdir(parents=True, exist_ok=True)

    def update(self, *, now_ts: float, state: str, frame_bgr: np.ndarray) -> None:
        if not self._cfg.enabled:
            return

        self.maybe_start(now_ts=now_ts, state=state, frame_bgr=frame_bgr)

        if self._writer is None:
            return

        if state == self._cfg.trigger_state:
            self._stop_deadline_ts = None
        else:
            if self._stop_deadline_ts is None:
                self._stop_deadline_ts = now_ts + float(self._cfg.stop_grace_seconds)
            if now_ts >= self._stop_deadline_ts:
                self.stop()
                return

        self.write_frame(frame_bgr)

    def maybe_start(self, *, now_ts: float, state: str, frame_bgr: np.ndarray) -> None:
        if state != self._cfg.trigger_state:
            return
        if self._writer is not None:
            return
        if self._cfg.cooldown_seconds > 0 and (now_ts - self._last_start_ts) < float(self._cfg.cooldown_seconds):
            return

        h, w = frame_bgr.shape[:2]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(self._cfg.assets_dir) / f"nomotion_{ts}"

        writer = self._open_writer(base.with_suffix(".mp4"), w=w, h=h, fps=self._cfg.fps, fourcc="mp4v")
        if writer is None:
            writer = self._open_writer(base.with_suffix(".avi"), w=w, h=h, fps=self._cfg.fps, fourcc="XVID")
            if writer is None:
                return

        self._writer = writer
        self._frames_left = max(1, int(round(float(self._cfg.clip_seconds) * float(self._cfg.fps))))
        self._last_start_ts = now_ts
        self._stop_deadline_ts = None

    def write_frame(self, frame_bgr: np.ndarray) -> None:
        if self._writer is None:
            return

        self._writer.write(frame_bgr)
        self._frames_left -= 1

        if self._frames_left <= 0:
            self.stop()

    def stop(self) -> None:
        writer = self._writer
        self._writer = None
        self._frames_left = 0
        self._stop_deadline_ts = None

        if writer is None:
            return

        try:
            writer.release()
        finally:
            pass

    @staticmethod
    def _open_writer(path: Path, *, w: int, h: int, fps: float, fourcc: str) -> Optional[cv2.VideoWriter]:
        codec = cv2.VideoWriter_fourcc(*fourcc)
        writer = cv2.VideoWriter(str(path), codec, float(fps), (int(w), int(h)))
        if not writer.isOpened():
            writer.release()
            return None
        return writer
