# main.py
from __future__ import annotations

from dataclasses import dataclass
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from analyzer.monitor_windows import set_process_dpi_awareness
from analyzer.capture import Region, ScreenCapturer
from analyzer.monitor_loop import DetectionParams, MonitorLoop
from config.config import load_config
from server.server import run_server_in_thread
from server.status_store import StatusStore
from ui.selector_ui import SelectorWindow, UiRegion


@dataclass
class SharedRegion:
    """
    Thread-safe container for the currently selected capture region.

    Why this exists:
    - The UI thread updates the selection (user resizes/moves the overlay).
    - The monitor loop thread reads the selection to know what part of the screen to capture.
    - A lock is required because reads/writes happen from different threads.
    """
    lock: threading.Lock
    region: Region


def main() -> int:
    set_process_dpi_awareness()

    cfg = load_config("./config/config.json")

    store = StatusStore(history_seconds=cfg.history_seconds)

    _server_thread = run_server_in_thread(
        host=cfg.server_host,
        port=cfg.server_port,
        store=store,
    )

    shared = SharedRegion(
        lock=threading.Lock(),
        region=Region(
            x=cfg.initial_region["x"],
            y=cfg.initial_region["y"],
            width=cfg.initial_region["width"],
            height=cfg.initial_region["height"],
        ),
    )

    def get_region() -> Region:
        with shared.lock:
            return Region(
                x=shared.region.x,
                y=shared.region.y,
                width=shared.region.width,
                height=shared.region.height,
            )

    def set_region(r: Region) -> None:
        with shared.lock:
            shared.region = r

    capturer = ScreenCapturer(cfg.capture_backend)

    loop = MonitorLoop(
        store=store,
        capturer=capturer,
        params=DetectionParams(
            fps=cfg.fps,
            diff_gain=cfg.diff_gain,
            no_motion_threshold=cfg.no_motion_threshold,
            low_activity_threshold=cfg.low_activity_threshold,
            ema_alpha=cfg.ema_alpha,
            mean_full_scale=cfg.mean_full_scale,
            tile_full_scale=cfg.tile_full_scale,
            grid_rows=cfg.grid_rows,
            grid_cols=cfg.grid_cols,
            record_enabled=cfg.recording_enabled,
            record_trigger_state=cfg.recording_trigger_state,
            record_clip_seconds=cfg.recording_clip_seconds,
            record_cooldown_seconds=cfg.recording_cooldown_seconds,
            record_assets_dir=cfg.recording_assets_dir,
        ),
        get_region=get_region,
    )
    loop.start()

    app = QApplication([])

    def on_close() -> None:
        store.request_quit()

    selector = SelectorWindow(
        initial=UiRegion(
            x=cfg.initial_region["x"],
            y=cfg.initial_region["y"],
            width=cfg.initial_region["width"],
            height=cfg.initial_region["height"],
        ),
        border_px=cfg.border_px,
        grid_line_px=cfg.grid_line_px,
        on_close=on_close,
        on_region_change=set_region,
        grid_rows=cfg.grid_rows,
        grid_cols=cfg.grid_cols,
        show_tile_numbers=store.get_show_tile_numbers(),
    )
    selector.show()

    timer = QTimer()
    timer.setInterval(200)

    def on_tick() -> None:
        if store.quit_requested():
            app.quit()
            return

        selector.set_show_tile_numbers(store.get_show_tile_numbers())

    timer.timeout.connect(on_tick)  # type: ignore[arg-type]
    timer.start()

    exit_code = app.exec()

    loop.stop()
    loop.join(timeout=2.0)

    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
