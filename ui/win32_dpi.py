"""ui/win32_dpi.py helpers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


# Win32 user32.dll access for per-window DPI queries.
_user32 = ctypes.windll.user32

# Declare argument/return types to avoid ctypes guessing.
_user32.GetDpiForWindow.argtypes = [wintypes.HWND]
_user32.GetDpiForWindow.restype = wintypes.UINT


def dpi_for_window(hwnd: int) -> int:
    """
    Return the per-window DPI as reported by Win32.

    Meaning:
    - 96 DPI == 100% scaling (1.0x)
    - 120 DPI == 125% scaling
    - 144 DPI == 150% scaling
    - etc.

    Why per-window:
    - On mixed-DPI setups, a window can move between monitors with different scaling.
    - The per-window DPI reflects the effective scale that should be used for that window.

    Fallback:
    - If the API is unavailable (older Windows) or fails, returns 96 (100%).
    """
    try:
        dpi = int(_user32.GetDpiForWindow(wintypes.HWND(hwnd)))
        return dpi if dpi > 0 else 96
    except Exception:
        return 96


def scale_for_window(hwnd: int) -> float:
    """
    Return the Win32 DPI scale factor for the given window.

    - 1.0 == 96 DPI (100%)
    - 1.25 == 120 DPI (125%)
    - 1.5 == 144 DPI (150%)

    This is typically used to convert UI "logical" pixel dimensions into physical pixels
    for capture backends and Win32 coordinate spaces.
    """
    return float(dpi_for_window(hwnd)) / 96.0
