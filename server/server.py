from __future__ import annotations

import threading
from typing import Any  # reserved for future expansion (kept if other modules import it)

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from server.server_html_contents import get_index_html
from server.status_store import StatusStore


class TileNumbersRequest(BaseModel):
    enabled: bool


def create_app(store: StatusStore) -> FastAPI:
    """Create the FastAPI application with routes bound to the provided shared StatusStore.

    The StatusStore is used as an in-memory state carrier between the analyzer loop and the UI/API.
    """
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """Serve the lightweight HTML dashboard (polls /status and /history)."""
        return HTMLResponse(get_index_html(history_seconds=120))

    @app.get("/status")
    async def status() -> JSONResponse:
        """Return the latest status payload (single snapshot)."""
        return JSONResponse(store.get_payload())

    @app.get("/history")
    async def history() -> JSONResponse:
        """Return the recent status payload history for charting/visualization."""
        return JSONResponse({"history": store.get_payload_history()})

    @app.get("/ui")
    async def ui_settings() -> JSONResponse:
        """Return server-controlled UI settings (e.g. tile number overlay)."""
        return JSONResponse(store.get_ui_settings())

    @app.post("/ui/tile-numbers")
    async def set_tile_numbers(req: TileNumbersRequest) -> JSONResponse:
        """Enable/disable tile numbers on the selector overlay (server-controlled)."""
        store.set_show_tile_numbers(req.enabled)
        return JSONResponse({"ok": True, "show_tile_numbers": store.get_show_tile_numbers()})

    @app.post("/quit")
    async def quit_app() -> JSONResponse:
        """Request a graceful shutdown of the overall application via the shared store flag."""
        store.request_quit()
        return JSONResponse({"ok": True})

    return app


def run_server_in_thread(*, host: str, port: int, store: StatusStore) -> threading.Thread:
    """Start the FastAPI server in a daemon thread so the main process can continue running."""
    app = create_app(store)

    def _run() -> None:
        """Thread entrypoint; blocks until uvicorn stops."""
        uvicorn.run(
            app,
            host=host,
            port=int(port),
            log_level="warning",
            access_log=False,
        )

    t = threading.Thread(target=_run, name="motiondetector-server", daemon=True)
    t.start()
    return t
