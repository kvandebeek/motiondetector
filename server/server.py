# File commentary: server/server.py - This file holds logic used by the motion detector project.
"""FastAPI application assembly and server-thread launcher.

This module exposes the runtime API consumed by both the browser dashboard and the
Qt overlay client. Endpoints are intentionally thin and delegate state ownership to
`StatusStore` so HTTP concerns remain separate from detection logic.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.server_html_contents import get_index_html
from server.status_store import StatusStore

# Static browser assets (index.html + JS modules + CSS).
# Resolved relative to this module so it works regardless of CWD.
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _parse_bool(value: Any) -> bool | None:
    """Parse bool and return normalized values for downstream code."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return None


def create_app(store: StatusStore, on_settings_changed=None, quality_clips_dir: str = "./assets/quality_clips") -> FastAPI:
    """
    Build the FastAPI application.

    Responsibilities:
    - Serve the browser UI (HTML + static assets).
    - Expose JSON endpoints consumed by the UI and by external clients:
        /status, /history, /tiles, /ui, /ui/tile-numbers, /quit
    - Keep all state in StatusStore so routes remain thin and deterministic.

    Notes:
    - This function is side-effect free besides mounting static files and registering routes.
    - Thread safety is handled by StatusStore; routes assume store methods are safe.
    """
    app = FastAPI()

    # Static file mounts:
    # - /assets is the canonical path for the UI.
    # - /server/assets is kept for backward compatibility with older index.html builds.
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")
    app.mount("/server/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="server-assets")
    clips_dir = Path(quality_clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/clips", StaticFiles(directory=str(clips_dir)), name="clips")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """
        Serve the UI HTML.

        The HTML is mostly static, but we inject small runtime configuration values
        (e.g., history window) via a simple placeholder replacement.
        """
        return HTMLResponse(get_index_html(history_seconds=int(store.get_history_seconds())))

    @app.get("/ui")
    async def get_ui() -> JSONResponse:
        """
        Return UI settings used by the browser client.

        This endpoint exists so the UI can initialize toggles/state even before it has
        fetched /status successfully.
        """
        return JSONResponse(store.get_ui_settings())

    @app.post("/ui/tile-numbers")
    async def ui_tile_numbers(body: dict[str, Any] = Body(default={})) -> JSONResponse:
        """
        Toggle whether tile numbers are rendered on the heatmap.

        Input JSON:
          { "enabled": true|false }

        Returns:
          - A small compatibility shape (`enabled`) for older clients
          - Plus the full UI settings as the authoritative current state
        """
        enabled_raw = body.get("enabled")
        if not isinstance(enabled_raw, bool):
            return JSONResponse({"error": "enabled must be a boolean"}, status_code=400)

        store.set_show_tile_numbers(enabled_raw)
        if callable(on_settings_changed):
            on_settings_changed(show_tile_numbers=enabled_raw)
        return JSONResponse({"enabled": store.get_show_tile_numbers(), **store.get_ui_settings()})

    @app.post("/ui/grid")
    async def ui_grid(body: dict[str, Any] = Body(default={})) -> JSONResponse:
        """Handle ui grid for this module."""
        rows = body.get("rows")
        cols = body.get("cols")
        if not isinstance(rows, int) or not isinstance(cols, int) or rows <= 0 or cols <= 0:
            return JSONResponse({"error": "rows and cols must be positive integers"}, status_code=400)
        store.set_grid(rows=rows, cols=cols)
        if callable(on_settings_changed):
            on_settings_changed(grid_rows=rows, grid_cols=cols)
        return JSONResponse(store.get_ui_settings())

    @app.post("/ui/state-overlay")
    async def ui_state_overlay(body: dict[str, Any] = Body(default={})) -> JSONResponse:
        """Handle ui state overlay for this module."""
        enabled = _parse_bool(body.get("enabled"))
        if enabled is None:
            return JSONResponse({"error": "enabled must be boolean"}, status_code=400)
        store.set_show_overlay_state(enabled)
        if callable(on_settings_changed):
            on_settings_changed(show_overlay_state=enabled)
        return JSONResponse(store.get_ui_settings())

    @app.post("/ui/region")
    async def ui_region(body: dict[str, Any] = Body(default={})) -> JSONResponse:
        """Handle ui region for this module."""
        x = body.get("x")
        y = body.get("y")
        width = body.get("width")
        height = body.get("height")
        if not all(isinstance(v, int) for v in [x, y, width, height]):
            return JSONResponse({"error": "x,y,width,height must be integers"}, status_code=400)
        if int(width) <= 0 or int(height) <= 0:
            return JSONResponse({"error": "width and height must be > 0"}, status_code=400)
        store.set_region(x=int(x), y=int(y), width=int(width), height=int(height))
        if callable(on_settings_changed):
            on_settings_changed(region_x=int(x), region_y=int(y), region_width=int(width), region_height=int(height))
        return JSONResponse(store.get_ui_settings())

    @app.get("/status")
    async def status() -> JSONResponse:
        """
        Return the latest status payload.

        This is the main endpoint polled by the browser UI and external clients.
        """
        return JSONResponse(store.get_payload())

    @app.get("/history")
    async def history() -> JSONResponse:
        """
        Return recent status samples as a list.

        The UI chart uses this endpoint to render a time-series view.
        """
        return JSONResponse({"history": store.get_payload_history()})

    @app.post("/quit")
    async def quit_app() -> JSONResponse:
        """
        Request application shutdown.

        The server itself does not exit the process; it signals via StatusStore so the
        main application loop can perform a clean shutdown (stop threads, release resources).
        """
        store.request_quit()
        return JSONResponse({"ok": True})

    @app.get("/tiles")
    async def get_tiles() -> JSONResponse:
        """
        Return the currently disabled tile indices (0-based).

        Used by the UI to initialize the mask and reconcile client/server state.
        """
        return JSONResponse({"disabled_tiles": store.get_disabled_tiles()})

    @app.get("/ui/settings")
    async def ui_settings() -> JSONResponse:
        """
        Compatibility alias for UI settings.

        Some older clients may call /ui/settings instead of /ui.
        """
        return JSONResponse(store.get_ui_settings())

    @app.get("/quality/events")
    async def quality_events() -> JSONResponse:
        """Return logged video-quality events."""
        getter = getattr(store, "get_quality_events", None)
        events = getter() if callable(getter) else []
        return JSONResponse({"events": events})

    @app.get("/quality/clips")
    async def quality_clips() -> JSONResponse:
        """List discovered quality clips."""
        clips: list[dict[str, str | int]] = []
        clips_dir = Path(quality_clips_dir)
        if clips_dir.exists():
            for p in sorted(clips_dir.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    clips.append({
                        "filename": p.name,
                        "url": f"/clips/{p.name}",
                        "size_bytes": int(p.stat().st_size),
                    })
                except OSError:
                    continue
        return JSONResponse({"clips": clips})

    @app.put("/tiles")
    async def put_tiles(body: dict[str, Any] = Body(...)) -> JSONResponse:
        """
        Replace the set of disabled tile indices (0-based).

        Input JSON:
          { "disabled_tiles": [0, 2, 8] }

        Validation:
        - Must be a list of integers. Range validation (0..N-1) is owned by the store
          or by the producer that knows N (grid size).
        """
        raw = body.get("disabled_tiles", [])
        if not isinstance(raw, list) or not all(isinstance(x, int) for x in raw):
            return JSONResponse({"error": "disabled_tiles must be a list[int]"}, status_code=400)

        store.set_disabled_tiles(raw)
        return JSONResponse({"disabled_tiles": store.get_disabled_tiles()})

    return app


def run_server_in_thread(*, host: str, port: int, store: StatusStore, quality_clips_dir: str = "./assets/quality_clips", on_settings_changed=None) -> threading.Thread:
    """
    Run the FastAPI server in a background thread.

    Why a thread:
    - The main application has its own UI event loop + monitor loop.
    - Running Uvicorn in a daemon thread keeps integration simple without additional processes.

    Notes:
    - `log_level="error"` keeps console noise low; adjust if debugging routing issues.
    - The returned thread is daemonized; application shutdown should be coordinated via
      StatusStore.quit_requested (or similar) in the main thread.
    """
    app = create_app(store, on_settings_changed=on_settings_changed, quality_clips_dir=quality_clips_dir)

    def _run() -> None:
        """Execute the main loop for this component until shutdown is requested."""
        # Uvicorn manages its own event loop internally.
        uvicorn.run(app, host=host, port=port, log_level="error")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
