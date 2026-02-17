from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Deque, Dict, List
from collections import deque


JsonDict = Dict[str, Any]  # JSON-like dict payload used across server/analyzer boundaries


@dataclass(frozen=True)
class StatusSample:
    # Single time-series datapoint kept in the rolling history.
    ts: float  # unix timestamp (seconds)
    payload: JsonDict  # raw status payload as produced by the analyzer loop


class StatusStore:
    """
    Thread-safe store for:
      - the latest status payload (for fast polling by the API/server)
      - a rolling window of status history (for charts/debugging/clients)
      - simple UI settings toggles (server-controlled)

    The analyzer thread typically calls set_latest(), while the web server thread
    calls get_latest()/get_history() concurrently.
    """

    def __init__(self, history_seconds: float) -> None:
        self._history_seconds = float(history_seconds)  # history retention window (seconds)
        self._lock = threading.Lock()  # protects all fields below
        self._latest: JsonDict = self._default_payload(reason="not_initialized")  # safe initial state
        self._history: Deque[StatusSample] = deque()  # append-only; trimmed by time window
        self._quit_requested = False  # shared shutdown flag for cooperating threads

        # UI settings (server-controlled)
        self._show_tile_numbers = True

    # ----------------------------
    # Status payload + history
    # ----------------------------

    def set_latest(self, payload: JsonDict) -> None:
        # Update latest payload + append to history, trimming old samples.
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        ts = float(payload.get("timestamp", time.time()))  # tolerate missing timestamps
        with self._lock:
            self._latest = payload  # keep raw payload (callers may already have nested dicts)
            self._history.append(StatusSample(ts=ts, payload=payload))  # store time-series sample
            self._trim_locked(now=ts)  # enforce rolling window

    def get_latest(self) -> JsonDict:
        # Snapshot latest payload (copy) so callers can't mutate internal state.
        with self._lock:
            return dict(self._latest)

    def get_payload(self) -> JsonDict:
        # Compatibility alias: server/server.py expects this method name.
        return self.get_latest()

    def get_history(self) -> List[JsonDict]:
        # Return a list of payloads within the retention window (trim first).
        now = time.time()
        with self._lock:
            self._trim_locked(now=now)
            return [s.payload for s in list(self._history)]  # preserve chronological order

    def get_payload_history(self) -> List[JsonDict]:
        # Compatibility alias: some callers use this method name.
        return self.get_history()

    # ----------------------------
    # UI settings
    # ----------------------------

    def set_show_tile_numbers(self, enabled: bool) -> None:
        with self._lock:
            self._show_tile_numbers = bool(enabled)

    def get_show_tile_numbers(self) -> bool:
        with self._lock:
            return bool(self._show_tile_numbers)

    def get_ui_settings(self) -> JsonDict:
        with self._lock:
            return {"show_tile_numbers": bool(self._show_tile_numbers)}

    # ----------------------------
    # Quit signalling
    # ----------------------------

    def request_quit(self) -> None:
        # Cooperative shutdown: set a flag that other threads can poll.
        with self._lock:
            self._quit_requested = True

    def quit_requested(self) -> bool:
        # Poll shutdown flag (thread-safe).
        with self._lock:
            return self._quit_requested

    # ----------------------------
    # Internals
    # ----------------------------

    def _trim_locked(self, now: float) -> None:
        # Remove samples older than (now - history_seconds).
        # Assumes caller holds self._lock.
        cutoff = float(now) - self._history_seconds
        while self._history and self._history[0].ts < cutoff:
            self._history.popleft()

    @staticmethod
    def _default_payload(*, reason: str) -> JsonDict:
        # Build a fully-populated payload with safe defaults so the UI/API
        # can render even before the analyzer loop starts producing real data.
        now = time.time()
        return {
            "timestamp": now,
            "capture": {"state": "ERROR", "reason": reason, "backend": "UNKNOWN"},
            "video": {
                "state": "ERROR",
                "confidence": 0.0,
                "motion_mean": 0.0,
                # Default tile keys for a 3x3 grid; keeps downstream consumers stable.
                "tile1": 0.0,
                "tile2": 0.0,
                "tile3": 0.0,
                "tile4": 0.0,
                "tile5": 0.0,
                "tile6": 0.0,
                "tile7": 0.0,
                "tile8": 0.0,
                "tile9": 0.0,
                # Bookkeeping fields used to detect stale data / last-change time.
                "last_phash_change_ts": 0.0,
                "last_update_ts": 0.0,
                "stale": True,
                "stale_age_sec": 0.0,
            },
            "overall": {"state": "NOT_OK", "reasons": [reason]},
            "errors": [reason],
            "region": {"x": 0, "y": 0, "width": 0, "height": 0},
        }
