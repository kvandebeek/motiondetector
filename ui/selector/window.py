"""Top-level Qt overlay window for region selection and tile interaction.

`SelectorWindow` composes specialized UI helpers (grid geometry, painting, interaction,
region emission, and server sync pollers) so user-facing behavior stays cohesive while
implementation details remain modular.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from analyzer.capture import Region
from ui.selector.chrome import ChromeConfig, ChromeUi
from ui.selector.grid import GridGeometry
from ui.selector.interaction import InteractionConfig, SelectorInteractor
from ui.selector.paint import PaintConfig, SelectorPainter
from ui.selector.region_emit import RegionEmitter
from ui.selector.ui_settings import UiSettingsPoller
from ui.tiles_sync import TilesSync
from ui.selector.models import UiRegion


class SelectorWindow(QWidget):
    """
    Transparent, frameless, always-on-top selector overlay window.

    Composition:
    - GridGeometry: computes inner rect and tile edges for painting + hit testing.
    - ChromeUi: renders/handles the top bar + close button geometry/hover.
    - SelectorInteractor: owns pointer behavior (move/resize/tile toggle) and triggers region emissions.
    - RegionEmitter: converts Win32 client rect into a capture Region in physical pixels.
    - SelectorPainter: draws border, grid lines, disabled overlays, optional tile labels, and chrome.

    External integrations:
    - TilesSync: polls a server endpoint for which tiles are disabled/enabled.
    - UiSettingsPoller (optional): polls a server endpoint for UI settings such as show_tile_numbers.

    Lifecycle:
    - On init: sets initial geometry and emits initial region.
    - During interaction: on drag/move/resize emits updated region via RegionEmitter.
    - Periodically: polls tiles (and optionally UI settings) and repaints on changes.
    """

    def __init__(
        self,
        *,
        initial: UiRegion,
        border_px: int,
        grid_line_px: int,
        on_close: Callable[[], None],
        on_region_change: Callable[[Region], None],
        grid_rows: int,
        grid_cols: int,
        emit_inset_px: int,
        tile_label_text_color: str,
        show_tile_numbers: bool,
        tiles_sync: TilesSync,
        tiles_poll_ms: int,
        http_timeout_sec: float,
        chrome_bar_h_px: int = 20,
        ui_settings_url: Optional[str] = None,
        ui_poll_ms: int = 250,
    ) -> None:
        super().__init__()

        # External callback invoked when the window is closed (by user or program).
        self._on_close = on_close

        # Local visual state; can be updated by UiSettingsPoller or programmatically.
        self._show_tile_numbers = bool(show_tile_numbers)

        # External tiles sync object (polls /tiles and exposes disabled_tiles set).
        self._tiles_sync = tiles_sync

        # Tile label styling; background uses fixed translucent black.
        # Foreground is parsed from user-provided string; fallback to white if invalid.
        self._tile_label_bg = QColor(0, 0, 0, 140)
        self._tile_label_fg = QColor(str(tile_label_text_color))
        if not self._tile_label_fg.isValid():
            self._tile_label_fg = QColor("#FFFFFF")
        self._tile_label_fg.setAlpha(230)

        # Disabled tile overlay styling.
        disabled_fill = QColor(255, 255, 255, 120)
        disabled_x_pen = QPen(QColor(20, 20, 20, 170))
        disabled_x_pen.setWidth(3)

        # Grid geometry (inner rect and tile edge computation).
        # Needs the same border/inset/chrome parameters used by RegionEmitter to keep paint + capture aligned.
        self._grid = GridGeometry(
            grid_rows=int(grid_rows),
            grid_cols=int(grid_cols),
            border_px=int(border_px),
            emit_inset_px=int(emit_inset_px),
            chrome_bar_h_px=int(chrome_bar_h_px),
        )

        # Chrome (top bar + close button geometry/hover + drawing).
        self._chrome = ChromeUi(
            ChromeConfig(
                chrome_bar_h_px=int(chrome_bar_h_px),
                chrome_gap_px=6,
                # Close button size scales with border, but never smaller than 24px for usability.
                chrome_btn_pref_px=max(24, int(border_px * 1.6)),
            )
        )

        # RegionEmitter uses Win32 client rect -> physical px Region.
        # The lambdas defer HWND/DPR lookup until runtime (useful during early init / monitor changes).
        self._region_emitter = RegionEmitter(
            win_id=lambda: int(self.winId()),
            qt_dpr=lambda: float(self.devicePixelRatioF()),
            screen_info=self._screen_info,
            on_region_change=on_region_change,
            border_px=int(border_px),
            emit_inset_px=int(emit_inset_px),
            chrome_bar_h_px=int(chrome_bar_h_px),
        )

        # Pointer interaction controller:
        # - move/resize window geometry
        # - toggle tiles on click
        # - emit region updates during drag/release
        self._interact = SelectorInteractor(
            widget=self,
            grid=self._grid,
            chrome=self._chrome,
            tiles=self._tiles_sync,
            region_emitter=self._region_emitter,
            on_close=self._handle_close,
            cfg=InteractionConfig(),
        )

        # Painter draws everything, given precomputed inner rect + edges.
        self._painter = SelectorPainter(
            cfg=PaintConfig(
                border_px=int(border_px),
                grid_line_px=int(grid_line_px),
                grid_rows=int(grid_rows),
                grid_cols=int(grid_cols),
                tile_label_fg=self._tile_label_fg,
                tile_label_bg=self._tile_label_bg,
                disabled_fill=disabled_fill,
                disabled_x_pen=disabled_x_pen,
            ),
            chrome=self._chrome,
        )

        # Optional UI settings poller (e.g. server-driven show_tile_numbers toggle).
        self._ui_poller: Optional[UiSettingsPoller] = None
        url = (ui_settings_url or "").strip()
        if url:
            p = UiSettingsPoller(url=url, poll_ms=int(ui_poll_ms), timeout_sec=float(http_timeout_sec), parent=self)
            p.valueChanged.connect(self.set_show_tile_numbers)  # type: ignore[arg-type]
            self._ui_poller = p

        # Window chrome/flags: frameless + always-on-top tool window, transparent background.
        self.setWindowTitle("motiondetector grid")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Required to receive mouse move events without pressing buttons.
        self.setMouseTracking(True)

        # Apply initial geometry and emit initial region right away.
        self.setGeometry(initial.x, initial.y, initial.width, initial.height)
        self._region_emitter.emit(reason="init")

        # Local import avoids a module-level dependency chain in some setups.
        from PySide6.QtCore import QTimer

        # Tiles polling:
        # The window requests tiles_sync.poll() on an interval and repaints if it changed.
        self._tiles_timer = QTimer(self)
        self._tiles_timer.setInterval(int(tiles_poll_ms))
        self._tiles_timer.timeout.connect(self._poll_tiles)  # type: ignore[arg-type]
        self._tiles_timer.start()
        self._poll_tiles()

        # Start server-driven UI settings polling, if configured.
        if self._ui_poller is not None:
            self._ui_poller.start()

    def set_show_tile_numbers(self, enabled: bool) -> None:
        """
        Toggle whether tile number labels are painted.

        Intended to be used as a slot for UiSettingsPoller.valueChanged,
        but can also be called directly.
        """
        v = bool(enabled)
        if v == self._show_tile_numbers:
            return
        self._show_tile_numbers = v
        self.update()

    def _handle_close(self) -> None:
        """
        Close handler used by the interactor when the close button is clicked.

        We:
        - invoke caller-provided on_close (to propagate shutdown intent),
        - request QApplication.quit() to stop the event loop if this is the only window.
        """
        self._on_close()
        QApplication.quit()

    def _screen_info(self) -> tuple[str, float, float]:
        """
        Collect screen diagnostics from Qt.

        Returns:
            (screen_name, logical_dpi, physical_dpi)

        This is primarily used by RegionEmitter diagnostics; region coordinates
        themselves come from Win32 client rect queries.
        """
        screen_name: str = "unknown"
        screen_logical: float = -1.0
        screen_phys: float = -1.0
        try:
            wh = self.windowHandle()
            sc = wh.screen() if wh is not None else None
            if sc is not None:
                screen_name = str(sc.name())
                screen_logical = float(sc.logicalDotsPerInch())
                screen_phys = float(sc.physicalDotsPerInch())
        except Exception:
            pass
        return screen_name, screen_logical, screen_phys

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """
        Qt close event.

        Stops timers/pollers and then propagates the close signal via _on_close.
        """
        try:
            self._tiles_timer.stop()
        except Exception:
            pass
        try:
            if self._ui_poller is not None:
                self._ui_poller.stop()
        except Exception:
            pass
        self._on_close()
        event.accept()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """
        Qt paint event.

        Uses GridGeometry to compute the inner rect and tile edges, then delegates to SelectorPainter.
        """
        _ = event
        p = QPainter(self)
        inner, x_edges, y_edges = self._grid.tile_rects(widget_rect=self.rect())
        self._painter.paint(
            p,
            widget_w=self.width(),
            widget_h=self.height(),
            inner=inner,
            x_edges=x_edges,
            y_edges=y_edges,
            show_tile_numbers=self._show_tile_numbers,
            disabled_tiles=set(self._tiles_sync.disabled_tiles),
        )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """
        Mouse press routing.

        Delegates to SelectorInteractor; if it handled the event, we may repaint immediately
        (e.g., close hover visuals can change on click).
        """
        pos = event.position().toPoint()
        global_pos = event.globalPosition().toPoint()
        if self._interact.on_mouse_press(button=event.button(), pos=pos, global_pos=global_pos):
            if self._chrome.close_hover:
                self.update()
            return

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        """
        Mouse move routing.

        Order matters:
        1) Update chrome hover (close button) and repaint if hover changed.
        2) If dragging, update geometry + emit region and repaint.
        3) Otherwise, update cursor shape based on hover/hit-test state.
        """
        pos = event.position().toPoint()
        global_pos = event.globalPosition().toPoint()

        if self._interact.update_hover(pos):
            self.update()

        moved = self._interact.on_mouse_move(pos=pos, global_pos=global_pos)
        if moved:
            self.update()
            return

        self._interact.set_cursor_for(pos=pos)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        """
        Mouse release routing.

        Ends any active drag mode and emits a final region update.
        """
        _ = event
        self._interact.on_mouse_release()

    def _poll_tiles(self) -> None:
        """
        Poll the tiles endpoint and repaint if the disabled_tiles set changed.
        """
        if self._tiles_sync.poll():
            self.update()
