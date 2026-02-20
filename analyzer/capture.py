"""Screen-capture abstractions and region utilities.

The code currently targets MSS while keeping backend handling explicit and typed. It
also centralizes virtual-desktop coordinate handling to reduce mixed-DPI/multi-monitor
surprises across the rest of the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Dict, Literal, Optional, Tuple

import mss
import numpy as np


# Capture backend(s) supported by this module.
# Using Literal keeps the rest of the codebase type-safe and prevents “magic string” backends.
Backend = Literal["MSS"]


@dataclass(frozen=True)
class Region:
    """
    Rectangle in virtual-desktop coordinates (pixels).

    Notes:
    - Coordinates are expected to be in the same coordinate space returned by MSS:
      the *virtual desktop* (monitor 0), where (0,0) is not guaranteed to be the
      top-left of the primary monitor on multi-monitor setups (negative coords can exist).
    - width/height are treated as pixel extents; callers may provide invalid values
      and we sanitize them in `grab()`.
    """
    x: int
    y: int
    width: int
    height: int


def _clamp_int(v: int, lo: int, hi: int) -> int:
    """
    Clamp integer v to [lo, hi].

    Rationale:
    - MSS will throw for out-of-bounds rectangles on some platforms.
    - Clamping also prevents negative/overflow coordinates from producing invalid capture boxes.
    """
    return lo if v < lo else hi if v > hi else v


def _region_center(r: Region) -> Tuple[int, int]:
    """
    Compute the integer center point of a region.

    We guard against width/height <= 0 by using max(1, …), so the center stays well-defined.
    """
    cx = int(r.x + max(1, r.width) // 2)
    cy = int(r.y + max(1, r.height) // 2)
    return cx, cy


def _pick_monitor_id(monitors: list[Dict[str, int]], r: Region) -> int:
    """
    Choose the MSS monitor index (>=1) that contains the region center.
    If none match (e.g. region straddles), return 0 (virtual monitor).

    Context:
    - MSS exposes monitors as:
        monitors[0] = virtual desktop bounding box
        monitors[1..N] = physical monitors
    - Picking a specific monitor can be useful for some backends/OSes, but in mixed-DPI
      Windows setups this can introduce scaling mismatches when switching monitors.
    """
    if len(monitors) <= 1:
        return 0

    cx, cy = _region_center(r)

    for i in range(1, len(monitors)):
        m = monitors[i]
        left = int(m.get("left", 0))
        top = int(m.get("top", 0))
        width = int(m.get("width", 0))
        height = int(m.get("height", 0))
        right = left + width
        bottom = top + height
        if left <= cx < right and top <= cy < bottom:
            return i

    return 0




def list_mss_monitors() -> list[dict[str, int]]:
    """Return MSS monitor rectangles including virtual desktop (index 0)."""
    try:
        with mss.mss() as sct:
            mons = list(sct.monitors)
    except Exception:
        return []

    out: list[dict[str, int]] = []
    for i, m in enumerate(mons):
        out.append({
            "id": int(i),
            "left": int(m.get("left", 0)),
            "top": int(m.get("top", 0)),
            "width": int(m.get("width", 0)),
            "height": int(m.get("height", 0)),
        })
    return out


def monitor_id_for_region(*, monitors: list[dict[str, int]], region: Region) -> int:
    """Pick monitor index whose bounds contain the region center (falls back to 0)."""
    if not monitors:
        return 0
    cx, cy = _region_center(region)
    for m in monitors:
        i = int(m.get("id", 0))
        if i <= 0:
            continue
        left = int(m.get("left", 0))
        top = int(m.get("top", 0))
        width = int(m.get("width", 0))
        height = int(m.get("height", 0))
        if left <= cx < left + width and top <= cy < top + height:
            return i
    return 0


def clamp_region_to_virtual_bounds(region: Region, *, monitors: list[dict[str, int]]) -> Region:
    """Clamp region to virtual monitor bounds (monitor id 0) and enforce positive size."""
    if not monitors:
        return Region(x=int(region.x), y=int(region.y), width=max(1, int(region.width)), height=max(1, int(region.height)))

    virt = next((m for m in monitors if int(m.get("id", -1)) == 0), monitors[0])
    vleft = int(virt.get("left", 0))
    vtop = int(virt.get("top", 0))
    vright = vleft + max(1, int(virt.get("width", 1)))
    vbottom = vtop + max(1, int(virt.get("height", 1)))

    x = _clamp_int(int(region.x), vleft, vright - 1)
    y = _clamp_int(int(region.y), vtop, vbottom - 1)
    x2 = _clamp_int(int(region.x) + max(1, int(region.width)), x + 1, vright)
    y2 = _clamp_int(int(region.y) + max(1, int(region.height)), y + 1, vbottom)
    return Region(x=x, y=y, width=max(1, x2 - x), height=max(1, y2 - y))

class ScreenCapturer:
    """
    Capture frames from a region.

    Why thread-local MSS:
    - On Windows, `mss.mss()` uses thread-local resources (device contexts). Reusing
      one instance across threads can trigger errors such as missing thread-local fields.
    - Therefore each thread must maintain its own MSS instance.

    Why “virtual desktop” capture:
    - Multi-monitor + mixed DPI scaling can cause coordinate mismatches if you capture
      from a specific monitor context that was created under different scaling settings.
    - Capturing via monitor 0 (the virtual desktop) keeps a stable coordinate space.
    """

    def __init__(self, backend: str) -> None:
        """
        Initialize a capturer for the requested backend.

        We accept a string for configuration-file friendliness, but normalize and validate it.
        """
        b = backend.strip().upper()
        if b != "MSS":
            raise ValueError(f"Unsupported capture backend: {backend!r} (expected 'MSS')")
        self._backend: Backend = "MSS"
        self._tls = threading.local()

    def _get_tls(self) -> tuple[mss.mss, Optional[int]]:
        """
        Return the thread-local (MSS instance, last_monitor_id).

        `last_mon` is included for future optimization (e.g. to detect monitor switches and
        reinitialize MSS if needed), but the current implementation captures on the virtual
        desktop so monitor switching is largely a non-issue.
        """
        sct: Optional[mss.mss] = getattr(self._tls, "sct", None)
        last_mon: Optional[int] = getattr(self._tls, "last_mon", None)

        if sct is None:
            # First use in this thread: create and cache a per-thread MSS instance.
            sct = mss.mss()
            self._tls.sct = sct
            self._tls.last_mon = None
            last_mon = None
            self._log_monitors(prefix="[mss:init]", sct=sct)

        return sct, last_mon

    @staticmethod
    def _log_monitors(*, prefix: str, sct: mss.mss) -> None:
        """
        Best-effort logging of monitor geometry.

        This is intentionally non-fatal: monitor enumeration can fail depending on platform,
        permissions, or transient OS state. Logging helps diagnose coordinate/DPI issues.
        """
        try:
            mons = list(sct.monitors)
        except Exception:
            return

        parts: list[str] = []
        for i, m in enumerate(mons):
            parts.append(
                f"{i}: left={int(m.get('left', 0))} top={int(m.get('top', 0))} "
                f"w={int(m.get('width', 0))} h={int(m.get('height', 0))}"
            )
        print(prefix, "monitors=", " | ".join(parts), flush=True)

    def grab(self, region: Region) -> np.ndarray:
        """
        Capture a frame and return it as BGRA uint8 ndarray with shape (H, W, 4).

        Coordinate space:
        - Uses *virtual desktop* coordinates (MSS monitor 0).

        Safety:
        - Sanitizes width/height to at least 1.
        - Clamps the capture box to the virtual desktop bounds when those bounds are available.
          This avoids MSS throwing for partially off-screen regions.
        """
        if self._backend != "MSS":
            # Defensive guard: should never happen unless someone bypasses __init__ validation.
            raise RuntimeError("Unsupported backend configured")

        sct, _last_mon = self._get_tls()

        # Sanitize region inputs.
        # Region may come from UI dragging where negative sizes or out-of-range values could occur.
        x = int(region.x)
        y = int(region.y)
        w = max(1, int(region.width))
        h = max(1, int(region.height))

        # Clamp to the virtual desktop bounds (monitor 0) if we can enumerate monitors.
        try:
            monitors = list(sct.monitors)
        except Exception:
            monitors = []

        if monitors:
            # monitors[0] is the virtual desktop bounding rectangle.
            virt = monitors[0]
            vleft = int(virt.get("left", 0))
            vtop = int(virt.get("top", 0))
            vright = vleft + int(virt.get("width", 0))
            vbottom = vtop + int(virt.get("height", 0))

            # Work in corner coordinates, clamp, then convert back to width/height.
            x2 = x + w
            y2 = y + h

            # Ensure top-left is inside the virtual bounds.
            x = _clamp_int(x, vleft, vright - 1)
            y = _clamp_int(y, vtop, vbottom - 1)

            # Ensure bottom-right is inside bounds and strictly beyond (x,y).
            x2 = _clamp_int(x2, x + 1, vright)
            y2 = _clamp_int(y2, y + 1, vbottom)

            w = max(1, x2 - x)
            h = max(1, y2 - y)

        # MSS expects a dict describing the capture rectangle.
        box = {"left": x, "top": y, "width": w, "height": h}

        # sct.grab returns an MSS “raw” image object; np.asarray converts it to an array view.
        # dtype=np.uint8 ensures downstream OpenCV/Numpy logic has predictable types.
        frame = np.asarray(sct.grab(box), dtype=np.uint8)
        return frame
