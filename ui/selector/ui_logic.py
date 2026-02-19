# ui/selector/ui_logic.py
from __future__ import annotations

import signal
import threading
from typing import Callable, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from analyzer.capture import Region
from ui.selector.window import SelectorWindow
from ui.selector.models import UiRegion
from ui.tiles_sync import TilesSync, TilesSyncConfig


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
    show_overlay_state: bool = False,
    server_base_url_override: Optional[str] = None,
    tiles_poll_ms: int = 500,
    http_timeout_sec: float = 0.35,
    on_window_geometry_change: Optional[Callable[[int, int, int, int], None]] = None,
    on_window_ready: Optional[Callable[[QApplication, SelectorWindow], None]] = None,
) -> None:
    """
    Start the selector overlay UI and block until the Qt event loop exits.

    on_window_ready (optional):
      Called after the SelectorWindow is created and shown.
      This is used to attach extra windows (e.g. --testdata) without refactoring the UI loop.
    """
    base = (server_base_url_override or "").strip().rstrip("/")
    if not base:
        raise ValueError("server_base_url_override must be provided (e.g. http://127.0.0.1:8735)")

    tiles_url = f"{base}/tiles"
    ui_settings_url = f"{base}/ui"

    tiles_sync = TilesSync(
        TilesSyncConfig(
            tiles_url=tiles_url,
            timeout_sec=float(http_timeout_sec),
            grid_rows=int(grid_rows),
            grid_cols=int(grid_cols),
        )
    )

    app = QApplication.instance() or QApplication([])

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
        show_overlay_state=bool(show_overlay_state),
        tiles_sync=tiles_sync,
        tiles_poll_ms=int(tiles_poll_ms),
        http_timeout_sec=float(http_timeout_sec),
        chrome_bar_h_px=45,
        ui_settings_url=ui_settings_url,
        ui_poll_ms=250,
        on_window_geometry_change=on_window_geometry_change,
    )
    w.show()

    # Allow callers (main.py) to attach additional windows once the overlay exists.
    if on_window_ready is not None:
        on_window_ready(app, w)


    prev_sigint = signal.getsignal(signal.SIGINT)

    def _handle_sigint(_signum, _frame) -> None:
        quit_flag.set()

    signal.signal(signal.SIGINT, _handle_sigint)

    quit_timer = QTimer()
    quit_timer.setInterval(1000/25)

    def on_quit_tick() -> None:
        try:
            if quit_flag.is_set():
                quit_timer.stop()
                w.close()
                app.quit()
        except KeyboardInterrupt:
            # On Windows, Ctrl-C can surface during Qt timer callbacks.
            # Treat it as a graceful shutdown signal instead of printing a traceback.
            quit_flag.set()
            quit_timer.stop()
            try:
                w.close()
            except Exception:
                pass
            app.quit()

    quit_timer.timeout.connect(on_quit_tick)  # type: ignore[arg-type]
    quit_timer.start()

    try:
        app.exec()
    except KeyboardInterrupt:
        # Graceful Ctrl-C handling while the Qt event loop is running.
        quit_flag.set()
        try:
            w.close()
        except Exception:
            pass
        app.quit()
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
