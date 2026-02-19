# ui/selector_ui_settings.py
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass(frozen=True)
class UiSettingsSnapshot:
    show_tile_numbers: bool
    show_overlay_state: bool
    region_x: int
    region_y: int
    region_width: int
    region_height: int
    current_state: str


class UiSettingsPoller(QObject):
    valueChanged = Signal(bool)
    settingsChanged = Signal(object)

    def __init__(
        self,
        *,
        url: str,
        poll_ms: int,
        timeout_sec: float,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._url = str(url).strip()
        self._poll_ms = int(poll_ms)
        self._timeout_sec = float(timeout_sec)

        self._lock = threading.Lock()
        self._in_flight = False
        self._last_value: Optional[bool] = None
        self._last_settings: Optional[UiSettingsSnapshot] = None

        self._timer = QTimer(self)
        self._timer.setInterval(self._poll_ms)
        self._timer.timeout.connect(self.poll)  # type: ignore[arg-type]

    def start(self) -> None:
        if not self._url:
            return
        self._timer.start()
        self.poll()

    def stop(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass

    def poll(self) -> None:
        if not self._url:
            return

        with self._lock:
            if self._in_flight:
                return
            self._in_flight = True

        url = self._url
        timeout_sec = self._timeout_sec

        def worker() -> None:
            try:
                req = Request(url=url, method="GET", headers={"Cache-Control": "no-store"})
                with urlopen(req, timeout=timeout_sec) as resp:
                    raw = resp.read()
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                data = None

            emit_bool: Optional[bool] = None
            emit_settings: Optional[UiSettingsSnapshot] = None
            with self._lock:
                self._in_flight = False
                if not isinstance(data, dict):
                    return

                show_numbers = data.get("show_tile_numbers")
                show_overlay = data.get("show_overlay_state")
                x = data.get("region_x")
                y = data.get("region_y")
                w = data.get("region_width")
                h = data.get("region_height")
                state = data.get("current_state")

                if not isinstance(show_numbers, bool):
                    return
                if self._last_value is None or show_numbers != self._last_value:
                    self._last_value = show_numbers
                    emit_bool = show_numbers

                if not isinstance(show_overlay, bool):
                    show_overlay = False
                if not isinstance(x, int):
                    x = 0
                if not isinstance(y, int):
                    y = 0
                if not isinstance(w, int):
                    w = 640
                if not isinstance(h, int):
                    h = 480
                if not isinstance(state, str):
                    state = "UNKNOWN"

                snap = UiSettingsSnapshot(
                    show_tile_numbers=show_numbers,
                    show_overlay_state=show_overlay,
                    region_x=int(x),
                    region_y=int(y),
                    region_width=max(1, int(w)),
                    region_height=max(1, int(h)),
                    current_state=state,
                )
                if self._last_settings is None or snap != self._last_settings:
                    self._last_settings = snap
                    emit_settings = snap

            if emit_bool is not None:
                self.valueChanged.emit(emit_bool)
            if emit_settings is not None:
                self.settingsChanged.emit(emit_settings)

        threading.Thread(target=worker, name="ui-settings-poll", daemon=True).start()
