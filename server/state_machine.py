from __future__ import annotations

from dataclasses import dataclass


# NOTE: Kept as `str` for simplicity, but semantically this is one of:
# "MOTION" | "LOW_ACTIVITY" | "NO_MOTION"
#
# Why `str`:
# - Keeps JSON serialization trivial and avoids extra conversions.
# - Lets configuration and UI code pass state values around without importing enums.
# If you later want stricter typing, you can replace this with a Literal[...] alias.
VideoState = str


@dataclass(frozen=True)
class MotionDecision:
    """
    Output of the motion state decision.

    This is deliberately small and immutable:
    - the analyzer computes motion metrics (continuous values)
    - this function maps those into a coarse state (discrete) + a confidence scalar

    Attributes:
        state:
            The derived video state. Expected values:
            - "NO_MOTION"
            - "LOW_ACTIVITY"
            - "MOTION"
        confidence:
            A scalar in [0..1] indicating how strongly the algorithm believes the chosen state.

            Interpretation depends on the state:
            - For NO_MOTION / LOW_ACTIVITY: confidence increases as motion_mean decreases.
              (i.e., “how sure are we that there is little/no motion?”)
            - For MOTION: confidence increases as motion_mean increases.
              (i.e., “how strong is the motion signal?”)
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

    Inputs represent two independent signals:
    - motion_mean: a continuous score (typically normalized ~[0..1]) summarizing activity.
    - all_tiles_no_motion: a boolean “hard gate” derived from tile-level analysis.

    Decision rules (priority order):
      1) Return NO_MOTION if either:
         - all_tiles_no_motion is True (tile-level hard gate), OR
         - motion_mean < no_motion_threshold (global threshold gate)

      2) Otherwise, return LOW_ACTIVITY if:
         - motion_mean < low_activity_threshold

      3) Otherwise, return MOTION.

    Args:
        motion_mean:
            Aggregated motion score (caller defines normalization; typically ~[0..1]).
        all_tiles_no_motion:
            True if tile-level logic concludes no motion across all tiles.
            This acts as a hard override to NO_MOTION.
        no_motion_threshold:
            If motion_mean is below this, classify as NO_MOTION.
        low_activity_threshold:
            If motion_mean is below this (but above no_motion_threshold), classify as LOW_ACTIVITY.

    Returns:
        MotionDecision:
            The chosen state and an associated confidence value.

    Notes:
        - This function assumes thresholds are ordered such that:
            no_motion_threshold <= low_activity_threshold
          If not, the LOW_ACTIVITY band may become unreachable or inverted.
        - Confidence is derived directly from motion_mean (no smoothing here):
            - For NO_MOTION / LOW_ACTIVITY: 1 - motion_mean
            - For MOTION: motion_mean
          This is intentionally simple and monotonic; if you want calibrated confidence,
          compute it upstream where you have more context (EMA, variance, tile distribution).
    """
    # Highest priority: tile-level hard gate or global "no motion" threshold.
    # Tile gate wins immediately (even if motion_mean is high) because it is meant to
    # represent a stricter per-tile consensus.
    if all_tiles_no_motion or motion_mean < float(no_motion_threshold):
        # Lower motion_mean => higher certainty of NO_MOTION.
        return MotionDecision(state="NO_MOTION", confidence=1.0 - float(motion_mean))

    # Not NO_MOTION; check for the "low activity" band between the two thresholds.
    if motion_mean < float(low_activity_threshold):
        # Still using inverted motion for confidence: lower motion => higher certainty.
        return MotionDecision(state="LOW_ACTIVITY", confidence=1.0 - float(motion_mean))

    # Above low-activity threshold: treat as motion.
    # Higher motion_mean => higher certainty.
    return MotionDecision(state="MOTION", confidence=float(motion_mean))
