"""Application composition root for the motion detector runtime.

This module wires together all subsystems:
- Windows DPI setup
- Config loading
- Shared state store
- FastAPI server thread
- Capture + monitor loop thread
- Qt overlay selector UI
- Optional synthetic test-data window

The goal is to keep cross-component lifecycle management in one place so the rest of
the codebase can remain focused on single responsibilities.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import threading
import traceback
from typing import Any

from analyzer.capture import Region, ScreenCapturer
from analyzer.monitor_loop import DetectionParams, MonitorLoop
from analyzer.monitor_windows import set_process_dpi_awareness
from config.config import load_config, patch_runtime_ui_motion_config
from server.server import run_server_in_thread
from server.status_store import StatusStore
from ui.selector.ui_logic import run_selector_ui
from ui.selector.models import UiRegion

# Testdata mode (new modules you’ll add)
from testdata.engine import TestDataEngine
from testdata.settings import TestDataSettings
from ui.testdata_window import TestDataWindow, TestDataWindowConfig
from ui.window_coupler import WindowCoupler


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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="motiondetector")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--testdata", action="store_true", help="Run testdata trainer (default profile).")
    g.add_argument("--testdata-fast", action="store_true", help="Run testdata trainer (fast profile).")
    g.add_argument("--testdata-slow", action="store_true", help="Run testdata trainer (slow profile).")
    p.add_argument("--testdata-seed", type=int, default=1337, help="Seed for deterministic testdata runs.")
    return p.parse_args()


def main() -> int:
    """
    Application entry point.

    High-level responsibilities:
    - Ensure Windows DPI awareness so Win32 pixel coordinates match physical pixels.
    - Load config (capture + detection + server + UI settings).
    - Start the HTTP server that exposes status and accepts UI/tile settings.
    - Start the monitor loop thread (continuous capture + motion detection).
    - Run the selector UI (Qt event loop) in the main thread.
    - Optionally run a synthetic testdata window aligned to the overlay and coupled to it.
    - Coordinate shutdown across server/UI/monitor threads via a shared quit flag.
    """
    args = _parse_args()

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
        show_tile_numbers=cfg.show_tile_numbers,
    )

    # Start FastAPI/uvicorn server in a background thread.
    _server_thread = run_server_in_thread(
        host=cfg.server_host,
        port=cfg.server_port,
        store=store,
        on_settings_changed=lambda **kwargs: patch_runtime_ui_motion_config("./config/config.json", **kwargs),
    )

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

    # Keep strong refs so Qt objects don’t get garbage-collected.
    # This matters for:
    # - TestDataWindow (Qt widget)
    # - WindowCoupler (QObject event filter)
    # - Engine (stateful scene generator)
    test_refs: dict[str, Any] = {}

    def on_close() -> None:
        """
        UI close callback.

        Responsibilities:
        - Signal the application should stop (quit_flag).
        - Inform the server/store so /quit state is consistent and quit watcher reacts.
        - Close testdata window if present.
        """
        quit_flag.set()
        try:
            store.request_quit()
        except Exception:
            traceback.print_exc()

        try:
            w = test_refs.get("test_window")
            if isinstance(w, TestDataWindow):
                w.close()
        except Exception:
            pass

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
    # Keep backwards compatibility for optional config knobs by using getattr defaults.
    params = DetectionParams(
        fps=float(cfg.fps),
        diff_gain=float(cfg.diff_gain),
        no_motion_threshold=float(cfg.no_motion_threshold),
        low_activity_threshold=float(cfg.low_activity_threshold),
        no_motion_grace_period_seconds=float(getattr(cfg, "no_motion_grace_period_seconds", 0.0)),
        no_motion_grace_required_ratio=float(getattr(cfg, "no_motion_grace_required_ratio", 1.0)),
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
        record_stop_grace_seconds=int(getattr(cfg, "record_stop_grace_seconds", 10)),
        analysis_inset_px=int(getattr(cfg, "analysis_inset_px", 0)),
        audio_enabled=bool(getattr(cfg, "audio_enabled", True)),
        audio_backend=str(getattr(cfg, "audio_backend", "pyaudiowpatch")),
        audio_device_substr=str(getattr(cfg, "audio_device_substr", "")),
        audio_samplerate=int(getattr(cfg, "audio_samplerate", 48_000)),
        audio_channels=int(getattr(cfg, "audio_channels", 2)),
        audio_block_ms=int(getattr(cfg, "audio_block_ms", 250)),
        audio_calib_sec=float(getattr(cfg, "audio_calib_sec", 2.0)),
        audio_factor=float(getattr(cfg, "audio_factor", 2.5)),
        audio_abs_min=float(getattr(cfg, "audio_abs_min", 0.00012)),
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

    def on_window_ready(app, selector_window) -> None:
        profile = "default"
        if bool(args.testdata_fast):
            profile = "fast"
        elif bool(args.testdata_slow):
            profile = "slow"
        elif bool(args.testdata):
            profile = "default"
        else:
            return

        td_settings = TestDataSettings.from_config(cfg)
        engine = TestDataEngine(settings=td_settings, seed=int(args.testdata_seed), profile_name=profile)

        test_window = TestDataWindow(
            engine=engine,
            cfg=TestDataWindowConfig(
                fps=float(cfg.fps),
                show_overlay_text=True,
                server_base_url=server_base_url,
                profile_name=profile,
                status_poll_ms=200 if profile == "fast" else 250 if profile == "default" else 400,
                log_dir="./testdata_logs",
                log_every_n_frames=1 if profile != "fast" else 2,
            ),
        )
        test_window.setGeometry(selector_window.geometry())
        test_window.show()

        coupler = WindowCoupler(a=selector_window, b=test_window)

        test_refs["engine"] = engine
        test_refs["test_window"] = test_window
        test_refs["coupler"] = coupler


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
        # Here it’s aligned with analysis_inset_px (inset inside the selected region).
        emit_inset_px=int(getattr(cfg, "analysis_inset_px", 0)),
        tile_label_text_color="#FFFFFF",
        show_tile_numbers=bool(cfg.show_tile_numbers),
        server_base_url_override=server_base_url,
        tiles_poll_ms=500,
        http_timeout_sec=0.35,
        on_window_ready=on_window_ready,
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
