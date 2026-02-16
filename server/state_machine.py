from __future__ import annotations

from dataclasses import dataclass


VideoState = str  # "MOTION" | "LOW_ACTIVITY" | "NO_MOTION"


@dataclass(frozen=True)
class MotionDecision:
    state: VideoState
    confidence: float


def decide_state(
    *,
    motion_mean: float,
    all_tiles_no_motion: bool,
    no_motion_threshold: float,
    low_activity_threshold: float,
) -> MotionDecision:
    if all_tiles_no_motion or motion_mean < float(no_motion_threshold):
        return MotionDecision(state="NO_MOTION", confidence=1.0 - float(motion_mean))

    if motion_mean < float(low_activity_threshold):
        # "OK, but clear low activity"
        return MotionDecision(state="LOW_ACTIVITY", confidence=1.0 - float(motion_mean))

    return MotionDecision(state="MOTION", confidence=float(motion_mean))
