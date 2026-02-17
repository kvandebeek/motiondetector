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
    # Master switch for recording (if False, recorder is a no-op).
    enabled: bool

    # The state that triggers/maintains recording (e.g. record when state == "NO_MOTION").
    trigger_state: str  # e.g. "NO_MOTION"

    # Maximum clip length in seconds (hard cap via frame budget: clip_seconds * fps).
    clip_seconds: int

    # Minimum time in seconds between starting two clips (prevents clip spam).
    cooldown_seconds: int

    # Target frames per second for the output video.
    fps: float

    # Output directory where clips will be saved.
    assets_dir: str

    # When state leaves trigger_state, keep recording up to this many seconds
    # before stopping (helps avoid rapid start/stop flapping).
    stop_grace_seconds: int = 10


class ClipRecorder:
    def __init__(self, cfg: RecorderConfig) -> None:
        self._cfg = cfg

        # Active OpenCV writer when recording; None when idle.
        self._writer: Optional[cv2.VideoWriter] = None

        # Remaining frames to write before auto-stopping (clip length cap).
        self._frames_left: int = 0

        # Timestamp (seconds) when the last clip started; used for cooldown.
        self._last_start_ts: float = 0.0

        # When we leave trigger_state, we set a deadline (now + grace).
        # If we re-enter trigger_state before the deadline, we clear it.
        self._stop_deadline_ts: Optional[float] = None

        # Ensure the output directory exists.
        Path(cfg.assets_dir).mkdir(parents=True, exist_ok=True)

    def update(self, *, now_ts: float, state: str, frame_bgr: np.ndarray) -> None:
        # Fast exit when recording is disabled.
        if not self._cfg.enabled:
            return

        # Start a new recording if the trigger state is active and we're idle.
        self.maybe_start(now_ts=now_ts, state=state, frame_bgr=frame_bgr)

        # If we still don't have a writer, there is nothing to do.
        if self._writer is None:
            return

        # Recording stop logic:
        # - While in trigger_state, keep recording and cancel any stop deadline.
        # - When leaving trigger_state, arm a stop deadline (grace period).
        # - If the grace period elapses, stop the recording.
        if state == self._cfg.trigger_state:
            self._stop_deadline_ts = None
        else:
            if self._stop_deadline_ts is None:
                self._stop_deadline_ts = now_ts + float(self._cfg.stop_grace_seconds)
            if now_ts >= self._stop_deadline_ts:
                self.stop()
                return

        # Write the current frame and enforce the max-clip-length cap.
        self.write_frame(frame_bgr)

    def maybe_start(self, *, now_ts: float, state: str, frame_bgr: np.ndarray) -> None:
        # Only start recording when the state matches the configured trigger.
        if state != self._cfg.trigger_state:
            return

        # Already recording.
        if self._writer is not None:
            return

        # Enforce cooldown between clip starts.
        if self._cfg.cooldown_seconds > 0 and (now_ts - self._last_start_ts) < float(self._cfg.cooldown_seconds):
            return

        # Determine output resolution from the incoming frame.
        h, w = frame_bgr.shape[:2]

        # Timestamp in the filename uses wall-clock time (not now_ts).
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(self._cfg.assets_dir) / f"nomotion_{ts}"

        # Prefer MP4 (mp4v); fall back to AVI (XVID) if the MP4 writer fails.
        writer = self._open_writer(base.with_suffix(".mp4"), w=w, h=h, fps=self._cfg.fps, fourcc="mp4v")
        if writer is None:
            writer = self._open_writer(base.with_suffix(".avi"), w=w, h=h, fps=self._cfg.fps, fourcc="XVID")
            if writer is None:
                # No supported codec/container available on this system.
                return

        # Recording is now active.
        self._writer = writer

        # Convert desired clip duration into a frame budget (at least 1 frame).
        self._frames_left = max(1, int(round(float(self._cfg.clip_seconds) * float(self._cfg.fps))))

        # Store start time for cooldown calculations.
        self._last_start_ts = now_ts

        # Clear any pending stop deadline (fresh recording).
        self._stop_deadline_ts = None

    def write_frame(self, frame_bgr: np.ndarray) -> None:
        # Guard against accidental calls when idle.
        if self._writer is None:
            return

        # Append frame to the output file.
        self._writer.write(frame_bgr)
        self._frames_left -= 1

        # Stop when we've reached the clip-length cap.
        if self._frames_left <= 0:
            self.stop()

    def stop(self) -> None:
        # Detach the writer first to avoid re-entrancy issues during release.
        writer = self._writer
        self._writer = None
        self._frames_left = 0
        self._stop_deadline_ts = None

        # Nothing to stop.
        if writer is None:
            return

        # Release the underlying file handle/encoder resources.
        try:
            writer.release()
        finally:
            # Kept explicit: release should be attempted even if it raises.
            pass

    @staticmethod
    def _open_writer(path: Path, *, w: int, h: int, fps: float, fourcc: str) -> Optional[cv2.VideoWriter]:
        # Convert the fourcc string into an OpenCV codec integer.
        codec = cv2.VideoWriter_fourcc(*fourcc)

        # Create the writer; OpenCV expects (width, height) as ints.
        writer = cv2.VideoWriter(str(path), codec, float(fps), (int(w), int(h)))

        # Validate that the writer is actually usable (codec available, etc.).
        if not writer.isOpened():
            writer.release()
            return None

        return writer
