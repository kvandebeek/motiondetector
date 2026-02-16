from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Deque, Dict, List
from collections import deque


JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class StatusSample:
    ts: float
    payload: JsonDict


class StatusStore:
    def __init__(self, history_seconds: float) -> None:
        self._history_seconds = float(history_seconds)
        self._lock = threading.Lock()
        self._latest: JsonDict = self._default_payload(reason="not_initialized")
        self._history: Deque[StatusSample] = deque()
        self._quit_requested = False

    def set_latest(self, payload: JsonDict) -> None:
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        ts = float(payload.get("timestamp", time.time()))
        with self._lock:
            self._latest = payload
            self._history.append(StatusSample(ts=ts, payload=payload))
            self._trim_locked(now=ts)

    def get_latest(self) -> JsonDict:
        with self._lock:
            return dict(self._latest)

    def get_payload(self) -> JsonDict:
        # server/server.py expects this name
        return self.get_latest()

    def get_history(self) -> List[JsonDict]:
        now = time.time()
        with self._lock:
            self._trim_locked(now=now)
            return [s.payload for s in list(self._history)]

    def get_payload_history(self) -> List[JsonDict]:
        return self.get_history()

    def request_quit(self) -> None:
        with self._lock:
            self._quit_requested = True

    def quit_requested(self) -> bool:
        with self._lock:
            return self._quit_requested

    def _trim_locked(self, now: float) -> None:
        cutoff = float(now) - self._history_seconds
        while self._history and self._history[0].ts < cutoff:
            self._history.popleft()

    @staticmethod
    def _default_payload(*, reason: str) -> JsonDict:
        now = time.time()
        return {
            "timestamp": now,
            "capture": {"state": "ERROR", "reason": reason, "backend": "UNKNOWN"},
            "video": {
                "state": "ERROR",
                "confidence": 0.0,
                "motion_mean": 0.0,
                "tile1": 0.0,
                "tile2": 0.0,
                "tile3": 0.0,
                "tile4": 0.0,
                "tile5": 0.0,
                "tile6": 0.0,
                "tile7": 0.0,
                "tile8": 0.0,
                "tile9": 0.0,
                "last_phash_change_ts": 0.0,
                "last_update_ts": 0.0,
                "stale": True,
                "stale_age_sec": 0.0,
            },
            "overall": {"state": "NOT_OK", "reasons": [reason]},
            "errors": [reason],
            "region": {"x": 0, "y": 0, "width": 0, "height": 0},
        }
