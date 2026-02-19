# File commentary: ui/win_geometry.py - This file holds logic used by the motion detector project.
# ui/win_geometry.py
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from ctypes import wintypes


# Win32 user32.dll access (used for coordinate conversion in physical pixels).
_user32 = ctypes.windll.user32

# Declare argument/return types for the Win32 APIs we call.
# This improves correctness and prevents ctypes from guessing types.
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.restype = wintypes.BOOL

_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.ClientToScreen.restype = wintypes.BOOL


@dataclass(frozen=True)
class WinRect:
    """
    Immutable rectangle representation using screen coordinates.

    The fields mirror Win32 RECT semantics:
    - left/top: inclusive origin
    - right/bottom: exclusive edge (commonly treated as left+width, top+height)

    This class also provides width/height convenience properties.
    """
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        """Width in pixels (right - left)."""
        return int(self.right - self.left)

    @property
    def height(self) -> int:
        """Height in pixels (bottom - top)."""
        return int(self.bottom - self.top)


def get_client_rect_in_screen_px(hwnd: int) -> WinRect:
    """
    Return the window client rect as *physical screen pixels* in virtual-desktop coordinates.

    Why this exists:
    - Qt global coordinates are device-independent and can shift across mixed-DPI monitors.
    - Screen capture backends (e.g., MSS) operate in physical virtual-desktop pixels.
    - This function provides a stable basis for capture region computations.

    Implementation:
    - GetClientRect yields the client size in client coordinates (0,0 .. width,height).
    - ClientToScreen converts the client origin (0,0) to screen coordinates (physical px).
    - We combine the converted origin with the client width/height to produce screen-space bounds.

    Raises:
        OSError: if either Win32 call fails (e.g., invalid hwnd, destroyed window).
    """
    # Fetch client RECT in client coordinates (origin is always 0,0 for the client area).
    rc = wintypes.RECT()
    ok = bool(_user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rc)))
    if not ok:
        raise OSError("GetClientRect failed")

    # Convert the client origin (0,0) to screen coordinates.
    pt = wintypes.POINT(0, 0)
    ok = bool(_user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)))
    if not ok:
        raise OSError("ClientToScreen failed")

    # Build the screen-space rect using the translated origin plus the client size.
    left = int(pt.x)
    top = int(pt.y)
    right = left + int(rc.right - rc.left)
    bottom = top + int(rc.bottom - rc.top)
    return WinRect(left=left, top=top, right=right, bottom=bottom)
