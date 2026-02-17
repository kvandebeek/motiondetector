# ui/selector_ui.py
from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from analyzer.capture import Region
from ui.selector.window import SelectorWindow
from ui.tiles_sync import TilesSync, TilesSyncConfig
from ui.selector.models import UiRegion


def run_selector_ui(
    *,
    initial: UiRegion,
    border_px: int,
    grid_line_px: int,
    on_close: Callable[[], None],
    on_region_change: Callable[[Region], None],
    quit_flag: threading.Event,
    grid_rows: int = 3,
    grid_cols: int = 3,
    emit_inset_px: int = 10,
    tile_label_text_color: str = "#FFFFFF",
    show_tile_numbers: bool = True,
    server_base_url_override: Optional[str] = None,
    tiles_poll_ms: int = 500,
    http_timeout_sec: float = 0.35,
) -> None:
    """
    Start (or attach to) the Qt application and show the selector overlay window.

    Responsibilities:
    - Build the base server URLs the overlay uses to:
      - fetch tile enable/disable state (GET /tiles)
      - fetch UI settings (GET /ui) such as show_tile_numbers
    - Construct a TilesSync instance to keep UI tile state in sync with the server.
    - Create and show SelectorWindow configured with grid/border/inset settings.
    - Poll a cross-thread quit_flag and close the overlay cleanly when requested.

    Threading model:
    - This function should be called from the UI thread.
    - quit_flag is set by another thread (e.g., main/monitor thread) to request shutdown.

    Notes:
    - server_base_url_override is required here because the selector window is designed to be
      remotely driven/configured via the HTTP server endpoints.
    """
    # Normalize base URL once; keep it stable (no trailing slash) for simple concatenation.
    base = (server_base_url_override or "").strip().rstrip("/")
    if not base:
        # The UI relies on the server to retrieve tiles/UI state; fail fast with a clear error.
        raise ValueError("server_base_url_override must be provided (e.g. http://127.0.0.1:8735)")

    # Endpoint for tile sync (expected to reflect current grid_rows/grid_cols configuration).
    tiles_url = f"{base}/tiles"

    # Endpoint for UI-specific settings (e.g. toggling tile number labels via server state).
    ui_settings_url = f"{base}/ui"  # JSON: {"show_tile_numbers": ...}

    # TilesSync periodically polls the server and exposes tile state to the window.
    tiles_sync = TilesSync(
        TilesSyncConfig(
            tiles_url=tiles_url,
            timeout_sec=float(http_timeout_sec),
            grid_rows=int(grid_rows),
            grid_cols=int(grid_cols),
        )
    )

    # If a QApplication already exists (common in embedded/hosted contexts), reuse it.
    # Otherwise, create a new one.
    app = QApplication.instance() or QApplication([])

    # Create the overlay window with all relevant UI/capture geometry parameters.
    w = SelectorWindow(
        initial=initial,
        border_px=int(border_px),
        grid_line_px=int(grid_line_px),
        on_close=on_close,
        on_region_change=on_region_change,
        grid_rows=int(grid_rows),
        grid_cols=int(grid_cols),
        emit_inset_px=int(emit_inset_px),
        tile_label_text_color=str(tile_label_text_color),
        show_tile_numbers=bool(show_tile_numbers),
        tiles_sync=tiles_sync,
        tiles_poll_ms=int(tiles_poll_ms),
        http_timeout_sec=float(http_timeout_sec),
        chrome_bar_h_px=45,
        ui_settings_url=ui_settings_url,
        ui_poll_ms=250,
    )
    w.show()

    # Quit polling:
    # We use a QTimer to periodically check the threading.Event without blocking the UI loop.
    # When set, we close the window and quit the Qt application.
    quit_timer = QTimer()
    quit_timer.setInterval(200)

    def on_quit_tick() -> None:
        if quit_flag.is_set():
            quit_timer.stop()
            w.close()
            app.quit()

    # Qt signal signature expects a callable; typing stubs sometimes mismatch, hence ignore.
    quit_timer.timeout.connect(on_quit_tick)  # type: ignore[arg-type]
    quit_timer.start()

    # Start the Qt event loop. This returns when app.quit() is called.
    app.exec()
