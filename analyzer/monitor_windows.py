from __future__ import annotations

import ctypes


def set_process_dpi_awareness() -> None:
    """
    Enable DPI awareness for the current process on Windows.

    Why this matters:
        On Windows, display scaling (e.g. 125%, 150%) can cause a DPI-unaware process to receive
        *virtualized* (scaled) coordinates. That makes screen coordinates and captured pixels not
        line up with what you see on the physical screen.

    What this does:
        - First tries the modern API (SetProcessDpiAwareness) to request *per-monitor* DPI awareness.
          This is preferred on multi-monitor setups where each display can have a different DPI.
        - If that API is not available or fails, falls back to the older system-wide API
          (SetProcessDPIAware).

    Notes:
        - Call this as early as possible (before creating windows / UI frameworks), because many
          toolkits read DPI state once at startup.
        - This function is Windows-specific; on non-Windows platforms the calls will fail and the
          function will return without doing anything.
    """
    try:
        # Modern DPI API hosted in shcore.dll (available on newer Windows versions).
        # Access via ctypes.windll to call into native Win32 DLL exports.
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]

        # SetProcessDpiAwareness values:
        #   0 = PROCESS_DPI_UNAWARE
        #   1 = PROCESS_SYSTEM_DPI_AWARE
        #   2 = PROCESS_PER_MONITOR_DPI_AWARE  (preferred)
        #
        # Using '2' makes coordinates align with physical pixels per monitor, reducing
        # coordinate/pixel mismatches for screen capture and overlay windows.
        shcore.SetProcessDpiAwareness(2)
    except Exception:
        # Fallback for older Windows versions that don't expose shcore.SetProcessDpiAwareness
        # (or if the call fails due to OS policy / already-initialized DPI state).
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]

            # Legacy API: enables system DPI awareness (not per-monitor), but still avoids the
            # most common scaling virtualization issues on single-DPI setups.
            user32.SetProcessDPIAware()
        except Exception:
            # If both calls fail (e.g. non-Windows, restricted environment), do nothing.
            return
