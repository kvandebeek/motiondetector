from __future__ import annotations

from dataclasses import dataclass


# NOTE: Kept as `str` for simplicity, but semantically this is one of:
# "MOTION" | "LOW_ACTIVITY" | "NO_MOTION"
VideoState = str


@dataclass(frozen=True)
class MotionDecision:
    """
    Output of the motion state decision.

    Attributes:
        state: The derived video state ("NO_MOTION", "LOW_ACTIVITY", "MOTION").
        confidence: A scalar in [0..1] indicating how strongly the algorithm
            believes the chosen state. Interpretation depends on the state:
            - For NO_MOTION / LOW_ACTIVITY: confidence increases as motion_mean decreases.
            - For MOTION: confidence increases as motion_mean increases.
    """

    state: VideoState
    confidence: float


def decide_state(
    *,
    motion_mean: float,
    all_tiles_no_motion: bool,
    no_motion_threshold: float,
    low_activity_threshold: float,
) -> MotionDecision:
    """
    Decide a coarse motion state based on a global motion score and a tile-level gate.

    Decision rules:
      1) Return NO_MOTION if either:
         - all_tiles_no_motion is True (tile-level hard gate), OR
         - motion_mean < no_motion_threshold (global threshold gate)

      2) Otherwise, return LOW_ACTIVITY if:
         - motion_mean < low_activity_threshold

      3) Otherwise, return MOTION.

    Args:
        motion_mean: Aggregated motion score (typically normalized ~[0..1]).
        all_tiles_no_motion: True if tile-level logic concludes no motion across all tiles.
            This is a hard override to NO_MOTION.
        no_motion_threshold: If motion_mean is below this, classify as NO_MOTION.
        low_activity_threshold: If motion_mean is below this (but above no_motion_threshold),
            classify as LOW_ACTIVITY.

    Returns:
        MotionDecision: The chosen state and an associated confidence value.

    Notes:
        - This function assumes thresholds are ordered such that:
            no_motion_threshold <= low_activity_threshold
        - Confidence is computed directly from motion_mean:
            - For NO_MOTION / LOW_ACTIVITY: 1 - motion_mean
            - For MOTION: motion_mean
    """
    # Hard "no motion" override:
    # - tile gate wins immediately (even if motion_mean is high)
    # - otherwise, fall back to global threshold
    if all_tiles_no_motion or motion_mean < float(no_motion_threshold):
        # Lower motion_mean => higher certainty of NO_MOTION.
        return MotionDecision(state="NO_MOTION", confidence=1.0 - float(motion_mean))

    # Not NO_MOTION; check for low activity band.
    if motion_mean < float(low_activity_threshold):
        # Still using inverted motion for confidence: lower motion => higher certainty.
        return MotionDecision(state="LOW_ACTIVITY", confidence=1.0 - float(motion_mean))

    # Above low-activity threshold: treat as motion.
    # Higher motion_mean => higher certainty.
    return MotionDecision(state="MOTION", confidence=float(motion_mean))
