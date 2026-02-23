"""analyzer/recorder.py helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
import threading
from typing import Deque, Optional, Tuple

from collections import deque

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

    # States that trigger/maintain recording.
    # Supports a single value ("NO_MOTION") or a comma-separated list
    # ("NO_MOTION,LOW_ACTIVITY").
    trigger_state: str

    # Kept for config compatibility (legacy cap is not used in incident mode).
    clip_seconds: int

    # Minimum time in seconds between starting two clips (prevents clip spam).
    cooldown_seconds: int

    # Target frames per second for the output video.
    fps: float

    # Output directory where clips will be saved.
    assets_dir: str

    # Post-roll after issue ends.
    stop_grace_seconds: int = 10

    # Pre-roll size in seconds.
    pre_roll_seconds: float = 2.0


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

        self._issue_prefixes = self._parse_trigger_prefixes(cfg.trigger_state)
        self._pre_roll_frames = max(1, int(np.ceil(float(cfg.fps) * float(cfg.pre_roll_seconds))))
        self._pre_roll_buffer: Deque[np.ndarray] = deque(maxlen=self._pre_roll_frames)

        self._session_active = False
        self._post_roll_deadline_ts: Optional[float] = None
        self._was_issue_active = False

        # Timestamp (seconds) when the last clip started; used for cooldown.
        self._last_start_ts: float = 0.0

        self._queue: Queue[Tuple[str, object]] = Queue(maxsize=max(120, self._pre_roll_frames * 4))
        self._writer_thread = threading.Thread(target=self._writer_worker, name="clip-recorder", daemon=True)
        self._writer_thread.start()

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

        issue_active = self._state_matches_trigger(state)

        if issue_active and not self._session_active:
            if self._cfg.cooldown_seconds <= 0 or (now_ts - self._last_start_ts) >= float(self._cfg.cooldown_seconds):
                self._start_session(frame_bgr=frame_bgr, now_ts=now_ts)

        if self._session_active:
            if issue_active:
                self._post_roll_deadline_ts = None
            elif self._was_issue_active:
                self._post_roll_deadline_ts = now_ts + float(self._cfg.stop_grace_seconds)

            self._enqueue_frame(frame_bgr=frame_bgr, issue_active=issue_active)

            if (not issue_active) and self._post_roll_deadline_ts is not None and now_ts >= self._post_roll_deadline_ts:
                self._finalize_session()

        self._pre_roll_buffer.append(frame_bgr.copy())
        self._was_issue_active = issue_active

    def _start_session(self, *, frame_bgr: np.ndarray, now_ts: float) -> None:
        h, w = frame_bgr.shape[:2]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(self._cfg.assets_dir) / f"nomotion_{ts}"

        if not self._enqueue(("start", (base, int(w), int(h), float(self._cfg.fps)))):
            return

        for buffered in list(self._pre_roll_buffer):
            self._enqueue_frame(frame_bgr=buffered, issue_active=False)

        self._session_active = True
        self._last_start_ts = now_ts
        self._post_roll_deadline_ts = None

    def _enqueue_frame(self, *, frame_bgr: np.ndarray, issue_active: bool) -> None:
        self._enqueue(("frame", (frame_bgr.copy(), bool(issue_active))))

    def _finalize_session(self) -> None:
        if not self._session_active:
            return
        self._enqueue(("stop", None))
        self._session_active = False
        self._post_roll_deadline_ts = None

    def stop(self) -> None:
        """
        Stop recording and release encoder/file resources.

        Implementation detail:
        - We detach the writer first so that if `release()` triggers any callbacks or raises,
          the recorder state is already consistent (idle).
        """
        self._finalize_session()
        self._enqueue(("shutdown", None))
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=2.0)

    @staticmethod
    def _open_writer(path: Path, *, w: int, h: int, fps: float, fourcc: str) -> Optional[cv2.VideoWriter]:
        codec = cv2.VideoWriter_fourcc(*fourcc)
        writer = cv2.VideoWriter(str(path), codec, float(fps), (int(w), int(h)))
        if not writer.isOpened():
            writer.release()
            return None
        return writer

    def _enqueue(self, item: Tuple[str, object]) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except Full:
            return False

    def _writer_worker(self) -> None:
        writer: Optional[cv2.VideoWriter] = None
        while True:
            try:
                kind, payload = self._queue.get(timeout=0.25)
            except Empty:
                continue

            if kind == "shutdown":
                break

            if kind == "start":
                if writer is not None:
                    writer.release()
                    writer = None
                base, w, h, fps = payload
                assert isinstance(base, Path)
                writer = self._open_writer(base.with_suffix(".mp4"), w=w, h=h, fps=fps, fourcc="mp4v")
                if writer is None:
                    writer = self._open_writer(base.with_suffix(".avi"), w=w, h=h, fps=fps, fourcc="XVID")
                continue

            if kind == "stop":
                if writer is not None:
                    writer.release()
                    writer = None
                continue

            if kind == "frame":
                if writer is None:
                    continue
                frame_bgr, issue_active = payload
                assert isinstance(frame_bgr, np.ndarray)
                out = frame_bgr
                if bool(issue_active):
                    out = self._with_issue_marker(frame_bgr)
                writer.write(out)

        if writer is not None:
            writer.release()

    @staticmethod
    def _with_issue_marker(frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        margin = max(10, int(round(min(w, h) * 0.015)))
        thickness = max(4, int(round(min(w, h) * 0.006)))
        x0, y0 = margin, margin
        x1, y1 = max(x0 + 1, w - margin), max(y0 + 1, h - margin)
        out = frame_bgr.copy()
        cv2.rectangle(out, (x0, y0), (x1, y1), color=(0, 255, 255), thickness=thickness)
        return out

    @staticmethod
    def _parse_trigger_prefixes(raw: str) -> Tuple[str, ...]:
        parts = [p.strip().upper() for p in str(raw).split(",") if p.strip()]
        if not parts:
            return ("NO_MOTION",)
        return tuple(parts)

    def _state_matches_trigger(self, state: str) -> bool:
        s = str(state).upper()
        return any(s == trig or s.startswith(f"{trig}_") for trig in self._issue_prefixes)
