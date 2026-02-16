from __future__ import annotations

import threading
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from server.server_html_contents import get_index_html
from server.status_store import StatusStore


def create_app(store: StatusStore) -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(get_index_html(history_seconds=120))

    @app.get("/status")
    async def status() -> JSONResponse:
        return JSONResponse(store.get_payload())

    @app.get("/history")
    async def history() -> JSONResponse:
        # List of status payloads (each payload already contains timestamp + motion_mean etc.)
        return JSONResponse({"history": store.get_payload_history()})

    @app.post("/quit")
    async def quit_app() -> JSONResponse:
        store.request_quit()
        return JSONResponse({"ok": True})

    return app


def run_server_in_thread(*, host: str, port: int, store: StatusStore) -> threading.Thread:
    app = create_app(store)

    def _run() -> None:
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
