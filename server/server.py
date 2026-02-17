from __future__ import annotations

import threading
from typing import Any  # reserved for future expansion (kept if other modules import it)

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from server.server_html_contents import get_index_html
from server.status_store import StatusStore


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
        # List of status payloads (each payload already contains timestamp + motion_mean etc.)
        return JSONResponse({"history": store.get_payload_history()})

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
            log_level="warning",  # keep logs minimal; main app can decide what to print
            access_log=False,  # avoid per-request access logs (status polling can be frequent)
        )

    t = threading.Thread(target=_run, name="motiondetector-server", daemon=True)
    t.start()
    return t
