# main.py
from __future__ import annotations

from dataclasses import dataclass
import threading
import traceback

from analyzer.capture import Region, ScreenCapturer
from analyzer.monitor_loop import DetectionParams, MonitorLoop
from analyzer.monitor_windows import set_process_dpi_awareness
from config.config import load_config
from server.server import run_server_in_thread
from server.status_store import StatusStore
from ui.selector.ui_logic import run_selector_ui
from ui.selector.models import UiRegion


@dataclass
class SharedRegion:
    """
    Thread-safe container for the currently selected capture region.

    Why this exists:
    - The UI thread updates the selection (user moves/resizes the overlay).
    - The monitor loop thread reads the selection to capture and analyze the correct screen area.
    - A lock is required because reads/writes happen concurrently.
    """
    lock: threading.Lock
    region: Region


def main() -> int:
    """
    Application entry point.

    High-level responsibilities:
    - Ensure Windows DPI awareness so Win32 pixel coordinates match physical pixels.
    - Load config (capture + detection + server + UI settings).
    - Start the HTTP server that exposes status and accepts UI/tile settings.
    - Start the monitor loop thread (continuous capture + motion detection).
    - Run the selector UI (Qt event loop) in the main thread.
    - Coordinate shutdown across server/UI/monitor threads via a shared quit flag.
    """
    # Important on Windows: makes per-window DPI queries and MSS coordinates consistent
    # (so capture regions align with what the user sees).
    set_process_dpi_awareness()

    # Configuration for server, capture, detection thresholds, initial overlay geometry, etc.
    cfg = load_config("./config/config.json")

    # Store holds latest payload + history and is shared by:
    # - monitor loop thread (writer)
    # - HTTP server (reader)
    # - quit watcher (reader)
    store = StatusStore(
        history_seconds=cfg.history_seconds,
        grid_rows=cfg.grid_rows,
        grid_cols=cfg.grid_cols,
    )

    # Start FastAPI/uvicorn server in a background thread.
    _server_thread = run_server_in_thread(host=cfg.server_host, port=cfg.server_port, store=store)

    # If server binds to 0.0.0.0, UI cannot call "http://0.0.0.0:PORT"; use loopback instead.
    server_host_for_ui = "127.0.0.1" if cfg.server_host == "0.0.0.0" else str(cfg.server_host)
    server_base_url = f"http://{server_host_for_ui}:{int(cfg.server_port)}"

    # Shared region starts from config initial region.
    # This is updated by the UI and read by the monitor loop.
    shared = SharedRegion(
        lock=threading.Lock(),
        region=Region(
            x=int(cfg.initial_region["x"]),
            y=int(cfg.initial_region["y"]),
            width=int(cfg.initial_region["width"]),
            height=int(cfg.initial_region["height"]),
        ),
    )

    # Quit coordination primitive used across threads:
    # - UI sets it on close
    # - quit watcher sets it when /quit is requested
    # - main uses it to stop the loop after UI returns
    quit_flag = threading.Event()

    def on_close() -> None:
        """
        UI close callback.

        Responsibilities:
        - Signal the application should stop (quit_flag).
        - Inform the server/store so /quit state is consistent and quit watcher reacts.
        """
        quit_flag.set()
        try:
            store.request_quit()
        except Exception:
            traceback.print_exc()

    def on_region_change(r: Region) -> None:
        """
        UI -> region update callback.

        Called frequently during drag, and once on release (depending on RegionEmitter usage).
        Must be lightweight and thread-safe.
        """
        with shared.lock:
            shared.region = r

    def get_region() -> Region:
        """
        MonitorLoop callback to fetch the latest region.

        Returns a snapshot; does not expose the mutable shared instance.
        """
        with shared.lock:
            return shared.region

    # Capture backend wrapper (currently MSS).
    capturer = ScreenCapturer(cfg.capture_backend)

    # Detection and recording parameters used by MonitorLoop.
    params = DetectionParams(
        fps=float(cfg.fps),
        diff_gain=float(cfg.diff_gain),
        no_motion_threshold=float(cfg.no_motion_threshold),
        low_activity_threshold=float(cfg.low_activity_threshold),
        ema_alpha=float(cfg.ema_alpha),
        mean_full_scale=float(cfg.mean_full_scale),
        tile_full_scale=float(cfg.tile_full_scale),
        grid_rows=int(cfg.grid_rows),
        grid_cols=int(cfg.grid_cols),
        record_enabled=bool(cfg.recording_enabled),
        record_trigger_state=str(cfg.recording_trigger_state),
        record_clip_seconds=int(cfg.recording_clip_seconds),
        record_cooldown_seconds=int(cfg.recording_cooldown_seconds),
        record_assets_dir=str(cfg.recording_assets_dir),
        # Optional config knobs with safe defaults for older configs.
        record_stop_grace_seconds=int(getattr(cfg, "record_stop_grace_seconds", 10)),
        analysis_inset_px=int(getattr(cfg, "analysis_inset_px", 0)),
    )

    # Continuous capture + analysis loop.
    loop = MonitorLoop(
        capturer=capturer,
        params=params,
        store=store,
        get_region=get_region,
    )

    # Start monitor thread before launching UI so status endpoints have data quickly.
    loop.start()

    def quit_watcher() -> None:
        """
        Background watcher that bridges server quit requests into the shared quit_flag.

        How it works:
        - Poll store.quit_requested() periodically.
        - If requested, set quit_flag so UI can shut down.
        - Then stop/join the monitor loop.

        This allows:
        - UI-close initiated shutdown (on_close sets quit_flag + store.request_quit)
        - server initiated shutdown (/quit sets store quit flag, watcher sets quit_flag)
        """
        while not quit_flag.is_set():
            try:
                if store.quit_requested():
                    quit_flag.set()
                    break
            except Exception:
                traceback.print_exc()

            # Wait with timeout so we can respond quickly without busy looping.
            quit_flag.wait(0.1)

        try:
            loop.stop()
            loop.join(timeout=1.0)
        except Exception:
            traceback.print_exc()

    threading.Thread(target=quit_watcher, name="quit-watcher", daemon=True).start()

    # Run selector UI (Qt event loop).
    # This call blocks until the window closes / app quits.
    run_selector_ui(
        initial=UiRegion(
            x=int(cfg.initial_region["x"]),
            y=int(cfg.initial_region["y"]),
            width=int(cfg.initial_region["width"]),
            height=int(cfg.initial_region["height"]),
        ),
        border_px=int(cfg.border_px),
        grid_line_px=int(cfg.grid_line_px),
        on_close=on_close,
        on_region_change=on_region_change,
        quit_flag=quit_flag,
        grid_rows=int(cfg.grid_rows),
        grid_cols=int(cfg.grid_cols),
        # emit_inset_px controls what part of the overlay is emitted as the capture region.
        # Here it's aligned with analysis_inset_px (inset inside the selected region).
        emit_inset_px=int(getattr(cfg, "analysis_inset_px", 0)),
        tile_label_text_color="#FFFFFF",
        show_tile_numbers=True,
        server_base_url_override=server_base_url,
        tiles_poll_ms=500,
        http_timeout_sec=0.35,
    )

    # UI returned: ensure shutdown is requested and monitor loop is stopped.
    quit_flag.set()
    try:
        loop.stop()
        loop.join(timeout=1.0)
    except Exception:
        traceback.print_exc()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
