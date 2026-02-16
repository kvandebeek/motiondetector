from __future__ import annotations

import ctypes


def set_process_dpi_awareness() -> None:
    """
    Ensure screen coordinates match physical pixels (important on Windows scaling).
    """
    try:
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            user32.SetProcessDPIAware()
        except Exception:
            return
