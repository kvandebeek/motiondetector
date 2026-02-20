"""analyzer/recorder.py helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class RecorderConfig:
    """
    Configuration for clip recording.

    Semantics:
    - Recording is edge-triggered + state-maintained:
      * A clip can start when `state == trigger_state` and cooldown allows.
      * While recording, leaving trigger_state does not stop immediately; a grace
        deadline is armed to prevent rapid start/stop flapping.
      * Recording always stops when the clip-length frame budget is exhausted.

    Practical note:
    - Codecs/containers depend on the OpenCV build and system codecs. The recorder tries
      a preferred option first and falls back to a more widely supported alternative.
    """

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
    """
    Simple stateful clip recorder driven by the monitor loop.

    API:
    - `update(...)` is called once per processed frame.
    - The recorder internally decides when to start/stop and writes frames accordingly.

    Threading:
    - Intended to be used from a single thread (the monitor loop thread). No locks are used.
    """

    def __init__(self, cfg: RecorderConfig) -> None:
        """Initialize this object with the provided inputs and prepare its internal state."""
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

        # Ensure the output directory exists early so start failures are codec-related,
        # not filesystem-related.
        Path(cfg.assets_dir).mkdir(parents=True, exist_ok=True)

    def update(self, *, now_ts: float, state: str, frame_bgr: np.ndarray) -> None:
        """
        Feed one frame into the recorder state machine.

        Inputs:
        - now_ts: monotonic-ish timestamp in seconds used for cooldown/grace logic (typically time.time()).
        - state: current motion classification state from the analyzer.
        - frame_bgr: frame to record, in BGR order (OpenCV convention), uint8.

        Behavior:
        - If disabled: no-op.
        - May start a new clip if conditions are met.
        - If recording: may stop if grace expires or clip length cap is reached.
        - If recording and still active: writes the provided frame.
        """
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
        if self._state_matches_trigger(state):
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
        """
        Start a new clip if:
        - state matches trigger_state
        - not already recording
        - cooldown has elapsed since the last clip start
        - a usable VideoWriter can be opened for at least one supported codec/container
        """
        # Only start recording when the state matches the configured trigger.
        if not self._state_matches_trigger(state):
            return

        # Already recording.
        if self._writer is not None:
            return

        # Enforce cooldown between clip starts.
        if self._cfg.cooldown_seconds > 0 and (now_ts - self._last_start_ts) < float(self._cfg.cooldown_seconds):
            return

        # Determine output resolution from the incoming frame.
        # OpenCV VideoWriter expects frames with exactly this size.
        h, w = frame_bgr.shape[:2]

        # Timestamp in the filename uses wall-clock time (human-friendly), not now_ts.
        # now_ts may be monotonic-ish or overridden; datetime.now() makes filenames predictable.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(self._cfg.assets_dir) / f"nomotion_{ts}"

        # Prefer MP4 (mp4v); fall back to AVI (XVID) if the MP4 writer fails.
        # This is a pragmatic choice: MP4 is convenient, AVI+XVID tends to work on more systems.
        writer = self._open_writer(base.with_suffix(".mp4"), w=w, h=h, fps=self._cfg.fps, fourcc="mp4v")
        if writer is None:
            writer = self._open_writer(base.with_suffix(".avi"), w=w, h=h, fps=self._cfg.fps, fourcc="XVID")
            if writer is None:
                # No supported codec/container available on this system/OpenCV build.
                return

        # Recording is now active.
        self._writer = writer

        # Convert desired clip duration into a frame budget (at least 1 frame).
        # Using frame budget makes the cap deterministic even if update cadence jitters.
        self._frames_left = max(1, int(round(float(self._cfg.clip_seconds) * float(self._cfg.fps))))

        # Store start time for cooldown calculations.
        self._last_start_ts = now_ts

        # Clear any pending stop deadline (fresh recording).
        self._stop_deadline_ts = None

    def write_frame(self, frame_bgr: np.ndarray) -> None:
        """
        Append a frame to the current clip and enforce the frame budget.

        Safety:
        - If called while idle, it is a no-op.
        """
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
        """
        Stop recording and release encoder/file resources.

        Implementation detail:
        - We detach the writer first so that if `release()` triggers any callbacks or raises,
          the recorder state is already consistent (idle).
        """
        # Detach the writer first to avoid re-entrancy issues during release.
        writer = self._writer
        self._writer = None
        self._frames_left = 0
        self._stop_deadline_ts = None

        # Nothing to stop.
        if writer is None:
            return

        # Release the underlying file handle/encoder resources.
        # OpenCV release normally does not raise, but this is wrapped defensively.
        try:
            writer.release()
        finally:
            # Kept explicit: release should be attempted even if it raises.
            pass

    @staticmethod
    def _open_writer(path: Path, *, w: int, h: int, fps: float, fourcc: str) -> Optional[cv2.VideoWriter]:
        """
        Try to open an OpenCV VideoWriter for the given path/codec.

        Returns:
        - A usable VideoWriter if the codec/container is supported and the file can be opened.
        - None otherwise.

        Notes:
        - `fourcc` must be a 4-character code understood by OpenCV for the current backend.
        - `isOpened()` is required: OpenCV can construct a writer object even when it cannot encode.
        """
        # Convert the fourcc string into an OpenCV codec integer.
        codec = cv2.VideoWriter_fourcc(*fourcc)

        # Create the writer; OpenCV expects (width, height) as ints.
        writer = cv2.VideoWriter(str(path), codec, float(fps), (int(w), int(h)))

        # Validate that the writer is actually usable (codec available, etc.).
        if not writer.isOpened():
            writer.release()
            return None

        return writer

    def _state_matches_trigger(self, state: str) -> bool:
        """State matches trigger."""
        s = str(state)
        trig = str(self._cfg.trigger_state)
        if s == trig:
            return True
        return s.startswith(f"{trig}_")
