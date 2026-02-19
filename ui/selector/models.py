# File commentary: ui/selector/models.py - This file holds logic used by the motion detector project.
# ui/ui_models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# Mouse interaction modes for the selector overlay.
# - "none": idle (no drag in progress)
# - "move": dragging the window (or general "inside" interactions)
# - edges/corners: resize handles (l/r/t/b + corners)
ResizeMode = Literal["none", "move", "l", "r", "t", "b", "tl", "tr", "bl", "br"]


@dataclass
class UiRegion:
    """
    UI-facing region model for the selector window.

    Coordinates are in Qt *logical* pixels (DPI-scaled units), not physical pixels.
    This keeps the UI consistent with Qt's coordinate system; conversion to physical
    pixels (if required for capture) should happen at the boundary with the capture layer.
    """
    # Initial window geometry in Qt *logical* pixels.
    x: int
    y: int
    width: int
    height: int


def round_int(x: float) -> int:
    """
    Round a float to the nearest int using Python's round() semantics.

    Used when converting between float-derived UI values and integer geometry,
    to avoid systematic bias from truncation.
    """
    return int(round(x))


def clamp_int(v: int, lo: int, hi: int) -> int:
    """
    Clamp an integer value to an inclusive range [lo, hi].

    Typical uses:
    - keeping geometry within screen bounds
    - constraining sizes to min/max limits
    - ensuring indices stay within valid ranges
    """
    return max(lo, min(hi, v))
