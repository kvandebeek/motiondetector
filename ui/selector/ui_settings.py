# ui/selector_ui_settings.py
from __future__ import annotations

import threading
from typing import Optional

import httpx
from PySide6.QtCore import QObject, QTimer, Signal


class UiSettingsPoller(QObject):
    """
    Periodically polls a UI settings endpoint and emits changes to subscribers.

    Current contract:
    - GET <url> returns JSON like: {"show_tile_numbers": true/false}
    - The poller extracts "show_tile_numbers" and emits valueChanged(bool) only when the value changes.

    Threading model:
    - QTimer runs on the Qt/UI thread and triggers poll().
    - Each poll spawns a short-lived daemon worker thread to perform the HTTP request so the UI thread
      never blocks on network I/O.
    - A lock + _in_flight flag prevents overlapping requests if the previous one hasn't completed yet.

    Failure behavior:
    - Any network/JSON/validation error results in a no-op (no signal emitted, last value unchanged).
    """

    # Emitted when the "show_tile_numbers" boolean value changes.
    valueChanged = Signal(bool)

    def __init__(
        self,
        *,
        url: str,
        poll_ms: int,
        timeout_sec: float,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)

        # Normalize once; empty URL disables polling.
        self._url = str(url).strip()
        self._poll_ms = int(poll_ms)
        self._timeout_sec = float(timeout_sec)

        # Guards request overlap and protects _in_flight/_last_value.
        self._lock = threading.Lock()
        self._in_flight = False

        # Last successfully parsed value; None means "unknown / never succeeded".
        self._last_value: Optional[bool] = None

        # Timer drives polling cadence on the Qt thread.
        self._timer = QTimer(self)
        self._timer.setInterval(self._poll_ms)
        self._timer.timeout.connect(self.poll)  # type: ignore[arg-type]

    def start(self) -> None:
        """
        Start polling.

        If url is empty, polling remains disabled.
        The first poll is executed immediately to avoid waiting one full interval.
        """
        if not self._url:
            return
        self._timer.start()
        self.poll()

    def stop(self) -> None:
        """
        Stop polling.

        Defensive try/except avoids edge cases during shutdown where the timer is already deleted.
        """
        try:
            self._timer.stop()
        except Exception:
            pass

    def poll(self) -> None:
        """
        Trigger a single poll.

        If a request is already in flight, this call is ignored (drop strategy).
        This keeps UI behavior predictable and avoids request pileups on slow/unstable networks.
        """
        if not self._url:
            return

        with self._lock:
            if self._in_flight:
                return
            self._in_flight = True

        # Copy values to locals so the worker doesn't depend on instance mutation mid-flight.
        url = self._url
        timeout_sec = self._timeout_sec

        def worker() -> None:
            # Perform the request and parse the boolean. Any failure yields val=None.
            try:
                with httpx.Client(timeout=timeout_sec) as client:
                    res = client.get(url, headers={"Cache-Control": "no-store"})
                    res.raise_for_status()
                    data = res.json()

                raw = data.get("show_tile_numbers") if isinstance(data, dict) else None
                val = raw if isinstance(raw, bool) else None
            except Exception:
                val = None

            # Decide whether to emit:
            # - always clear _in_flight
            # - only emit when we got a valid boolean AND it changed since last time.
            emit: Optional[bool] = None
            with self._lock:
                self._in_flight = False

                # Invalid response: keep last known value, emit nothing.
                if val is None:
                    return

                if self._last_value is None or val != self._last_value:
                    self._last_value = val
                    emit = val

            # Qt signal emit is thread-safe in PySide; connected slots run on receiver thread (UI) as needed.
            if emit is not None:
                self.valueChanged.emit(emit)

        # Daemon thread ensures app shutdown is not blocked by a lingering poll request.
        threading.Thread(target=worker, name="ui-settings-poll", daemon=True).start()
