"""ui/selector/region_emit.py helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from analyzer.capture import Region

from ui.win32_dpi import dpi_for_window, scale_for_window
from ui.win_geometry import get_client_rect_in_screen_px


@dataclass
class _DpiDiagState:
    """
    Simple "last seen" cache used to avoid spamming DPI diagnostics logs.

    We log only when:
    - the window moved to a different screen, or
    - the Win32 DPI for the window changed, or
    - Qt's device pixel ratio changed.

    This helps debug mixed-DPI monitor setups and basis changes without flooding stdout.
    """
    last_screen_name: Optional[str] = None
    last_win_dpi: Optional[int] = None
    last_qt_dpr: Optional[float] = None


class RegionEmitter:
    """
    Converts the overlay widget's *window client rect* into a capture Region (physical screen pixels)
    and emits it to downstream consumers.

    Why Win32 is used here (instead of Qt global geometry):
    - Qt global coordinates are device-independent and can shift coordinate basis across mixed-DPI monitors.
    - Capture backends like MSS operate in physical virtual-desktop pixels.
    - Using the Win32 client rect in screen pixels gives stable, backend-compatible coordinates.

    The emitted region:
    - is inset from the client rect by (border_px + emit_inset_px) on all sides
    - excludes the chrome bar height at the top (chrome_bar_h_px)
    - is always expressed in physical pixels.
    """

    def __init__(
        self,
        *,
        win_id: Callable[[], int],
        qt_dpr: Callable[[], float],
        screen_info: Callable[[], tuple[str, float, float]],  # (name, logical_dpi, physical_dpi)
        on_region_change: Callable[[Region], None],
        border_px: int,
        emit_inset_px: int,
        chrome_bar_h_px: int,
    ) -> None:
        # Provider for HWND (Win32 window handle) of the overlay.
        self._win_id = win_id

        # Provider for Qt device pixel ratio (useful for diagnostics; not the source of truth for region coords).
        self._qt_dpr = qt_dpr

        # Provider for current screen name + DPI values from Qt (diagnostics only).
        self._screen_info = screen_info

        # Callback invoked with the computed Region.
        self._on_region_change = on_region_change

        # Logical-pixel settings that define what part of the client area is considered "capturable".
        self._border_px = int(border_px)
        self._emit_inset_px = int(emit_inset_px)
        self._chrome_bar_h_px = int(chrome_bar_h_px)

        # Diagnostic "last log" state to prevent log spam.
        self._dbg = _DpiDiagState()

    def _log_dpi_if_changed(self, *, reason: str) -> None:
        """
        Print a one-line DPI diagnostic whenever key DPI-related inputs change.

        This helps confirm that:
        - Win32 DPI awareness/scaling is behaving as expected,
        - Qt DPR is stable or changes when moving between monitors,
        - Qt's reported screen logical DPI aligns with DPR and physical DPI expectations.

        The data printed here is diagnostic only; region computation uses Win32 client px.
        """
        hwnd = int(self._win_id())
        win_dpi = dpi_for_window(hwnd)
        qt_dpr = float(self._qt_dpr())

        screen_name, screen_logical, screen_phys = self._screen_info()
        changed = (
            self._dbg.last_screen_name != screen_name
            or self._dbg.last_win_dpi != win_dpi
            or self._dbg.last_qt_dpr != qt_dpr
        )
        if not changed:
            return

        self._dbg.last_screen_name = screen_name
        self._dbg.last_win_dpi = win_dpi
        self._dbg.last_qt_dpr = qt_dpr

        # A rough expectation check: logical DPI * DPR should be close to "effective" DPI.
        expected_from_qt = screen_logical * qt_dpr if screen_logical > 0 else -1.0

        """
        print(
            "[dpi]",
            "reason=",
            reason,
            "screen=",
            screen_name,
            "win_dpi=",
            win_dpi,
            "scale=",
            round(scale_for_window(hwnd), 4),
            "qt_dpr=",
            round(qt_dpr, 4),
            "qt_screen_logical_dpi=",
            round(screen_logical, 2),
            "qt_screen_physical_dpi=",
            round(screen_phys, 2),
            "expected_from_qt(logical*dpr)=",
            round(expected_from_qt, 2),
            flush=True,
        )
        """

    def emit(self, *, reason: str) -> None:
        """
        Emit capture region in *physical screen pixels*.

        Implementation steps:
        1) Query the Win32 client rect in screen coordinates (already in physical px).
        2) Convert configured logical inset/chrome sizes into physical px using the window scale factor.
        3) Apply:
           - inset on all sides,
           - chrome bar height at the top,
           - then compute x/y/w/h.
        4) Validate (w/h >= 1) and emit Region.

        Failure modes:
        - If the HWND is invalid/closing, get_client_rect_in_screen_px may raise OSError; we bail out silently.
        """
        self._log_dpi_if_changed(reason=reason)

        hwnd = int(self._win_id())
        try:
            # client is in physical screen pixels: left/top (screen coords), width/height (px)
            client = get_client_rect_in_screen_px(hwnd)
        except OSError:
            # Window may have been destroyed or not ready.
            return

        # Win32 scale factor for this window; used to convert logical UI pixels -> physical pixels.
        scale = scale_for_window(hwnd)

        # The inset is specified in logical pixels; convert to physical pixels.
        inset_logical = max(0, self._border_px + self._emit_inset_px)
        inset_px = int(round(float(inset_logical) * scale))

        # Chrome bar is logical height; convert to physical pixels too.
        chrome_px = int(round(float(self._chrome_bar_h_px) * scale))

        # Compute the final capture region:
        # - start at client left/top
        # - skip chrome at the top
        # - inset inside borders on all sides
        x = client.left + inset_px
        y = client.top + chrome_px + inset_px
        w = client.width - (2 * inset_px)
        h = client.height - chrome_px - (2 * inset_px)

        # Verbose diagnostics for troubleshooting "off by N px" issues (e.g., mixed DPI, border math).
        print(
            "[emit_region]",
            "reason=",
            reason,
            "client=",
            (client.left, client.top, client.width, client.height),
            "region=",
            (x, y, w, h),
            "scale=",
            round(scale, 4),
            "inset_logical=",
            inset_logical,
            "inset_px=",
            inset_px,
            "chrome_px=",
            chrome_px,
            flush=True,
        )

        # Guard against degenerate geometry.
        if w < 1 or h < 1:
            return

        # Emit Region in physical pixels (required by capture backend).
        self._on_region_change(Region(x=int(x), y=int(y), width=int(w), height=int(h)))
