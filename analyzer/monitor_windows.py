# File commentary: analyzer/monitor_windows.py - This file holds logic used by the motion detector project.
from __future__ import annotations

import ctypes


def set_process_dpi_awareness() -> None:
    """
    Enable DPI awareness for the current process on Windows.

    Why this matters:
    - If a process is DPI-unaware, Windows may apply DPI virtualization (coordinate scaling).
      That leads to mismatches between:
        * UI overlay geometry (Qt/PySide window coordinates)
        * screen-capture coordinates (MSS / Win32)
        * the physical pixels on the monitor
    - Mixed-DPI multi-monitor setups (e.g., laptop + external display) are especially sensitive:
      each monitor can have a different effective DPI.

    What this does:
    - Prefer the modern "per-monitor DPI aware" API (shcore.SetProcessDpiAwareness).
      This is the most accurate mode for per-monitor scaling.
    - If unavailable or failing, fall back to the legacy user32.SetProcessDPIAware(), which
      at least disables most DPI virtualization on single-DPI setups.

    Operational notes:
    - Call as early as possible (before creating any UI windows). Many UI toolkits cache DPI
      state at initialization time; calling late can be ineffective.
    - Windows-only: on non-Windows platforms, these calls will raise and we intentionally
      no-op via the exception handling.
    """
    try:
        # Modern DPI API hosted in shcore.dll (available on newer Windows versions).
        # ctypes.windll resolves DLL exports as attributes.
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]

        # SetProcessDpiAwareness values (PROCESS_DPI_AWARENESS enum):
        #   0 = PROCESS_DPI_UNAWARE
        #   1 = PROCESS_SYSTEM_DPI_AWARE
        #   2 = PROCESS_PER_MONITOR_DPI_AWARE  (preferred)
        #
        # Using 2 aligns logical coordinates with physical pixels per monitor.
        shcore.SetProcessDpiAwareness(2)
    except Exception:
        # Fallback for:
        # - Older Windows versions (no shcore.dll export)
        # - Environments where DPI awareness is already set and the call errors
        # - Policies that block the newer API
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]

            # Legacy API: system DPI aware (not per-monitor), but still prevents the most common
            # DPI virtualization problems for many single-monitor and uniform-DPI setups.
            user32.SetProcessDPIAware()
        except Exception:
            # Non-Windows or restricted environment: safe no-op.
            return
