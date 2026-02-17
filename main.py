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
    """
    Application entry point.

    Responsibilities:
    - Set Windows DPI awareness so screen coordinates match physical pixels.
    - Load configuration.
    - Start the status store + API server for live telemetry.
    - Start the monitor loop that captures frames and computes motion statistics.
    - Start the PySide6 UI overlay that lets the user select the region being monitored.
    - Handle shutdown by coordinating UI -> store quit flag -> monitor loop stop/join.
    """
    # Ensure Windows scaling (125%/150%) does not distort screen coordinates.
    # Without this, the selected overlay region can mismatch the captured pixels.
    set_process_dpi_awareness()

    # Load runtime settings (capture backend, thresholds, grid size, server port, etc.).
    cfg = load_config("./config/config.json")

    # Shared in-memory state for telemetry/history and quit signalling.
    # MonitorLoop writes status snapshots into the store; the server reads from it.
    store = StatusStore(history_seconds=cfg.history_seconds)

    # Start the FastAPI/uvicorn server in a background thread.
    # The returned thread is stored to prevent accidental garbage collection and
    # to document intent; the process lifetime is driven by the Qt event loop.
    _server_thread = run_server_in_thread(
        host=cfg.server_host,
        port=cfg.server_port,
        store=store,
    )

    # Initialize the capture region from config, but allow the UI to mutate it at runtime.
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
        """
        Called by the monitor loop thread to obtain the latest region.

        Returns a defensive copy so callers never hold a reference to mutable shared state.
        """
        with shared.lock:
            return Region(
                x=shared.region.x,
                y=shared.region.y,
                width=shared.region.width,
                height=shared.region.height,
            )

    def set_region(r: Region) -> None:
        """
        Called by the UI thread when the user moves/resizes the overlay selection.
        """
        with shared.lock:
            shared.region = r

    # Create a screen capturer for the chosen backend (e.g. WGC / MSS / etc.).
    capturer = ScreenCapturer(cfg.capture_backend)

    # Start the monitoring loop:
    # - Captures the selected region at cfg.fps.
    # - Computes motion signals (mean + per-tile).
    # - Updates StatusStore history.
    # - Optionally triggers recording based on state transitions.
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

    # Start the Qt application (UI must run on the main thread).
    app = QApplication([])

    def on_close() -> None:
        """
        Called when the selector window is closed.
        Instead of hard-exiting immediately, we request a quit in the shared store.
        The QTimer below observes that flag and exits the Qt event loop safely.
        """
        store.request_quit()

    # Transparent always-on-top overlay for selecting the region and visualizing grid lines.
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
    )
    selector.show()

    # Poll for shutdown request without busy-waiting.
    # This avoids cross-thread UI calls; Qt stays responsive and we exit cleanly.
    timer = QTimer()
    timer.setInterval(200)

    # If quit was requested (e.g. window closed), stop the Qt event loop.
    timer.timeout.connect(lambda: app.quit() if store.quit_requested() else None)
    timer.start()

    # Blocks here until app.quit() is called or the last window closes.
    exit_code = app.exec()

    # Post-UI shutdown: stop monitoring thread and wait briefly for it to terminate.
    loop.stop()
    loop.join(timeout=2.0)

    return int(exit_code)


if __name__ == "__main__":
    # Convert the returned int into a proper process exit code.
    raise SystemExit(main())
