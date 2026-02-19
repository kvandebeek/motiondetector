# File commentary: ui/ui_sync.py - This file holds logic used by the motion detector project.
# ui/ui_sync.py
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass(frozen=True)
class UiSyncConfig:
    """
    Configuration for polling UI-related settings from the server.

    - ui_url: endpoint returning UI JSON (e.g. {"show_tile_numbers": true})
    - timeout_sec: HTTP client timeout used for each request
    """
    ui_url: str  # e.g. http://127.0.0.1:8735/ui
    timeout_sec: float


class UiSync:
    """
    Lightweight polling helper for UI settings stored on the server.

    Purpose:
    - Fetch the latest UI settings and surface changes to the caller without emitting signals.

    Current setting supported:
    - show_tile_numbers (bool)

    Behavior:
    - poll_show_tile_numbers() returns a bool only when a *new* value is observed
      (first successful fetch counts as "new"), otherwise returns None.
    - reset() clears cached state so the next successful poll returns the fetched value again.

    Thread-safety:
    - Uses a lock to protect _last_show_tile_numbers in case polling is performed across threads.
    - HTTP request is performed outside the lock to avoid blocking other callers.
    """
    def __init__(self, cfg: UiSyncConfig) -> None:
        """Initialize this object with the provided inputs and prepare its internal state."""
        self._cfg = cfg
        self._lock = threading.Lock()
        self._last_show_tile_numbers: Optional[bool] = None

    def poll_show_tile_numbers(self) -> Optional[bool]:
        """
        Poll the server for the 'show_tile_numbers' UI setting.

        Returns:
          - bool: when a valid value was fetched and differs from the last seen value
                  (the first successful fetch also returns the fetched value)
          - None: when unchanged, missing/invalid, or request failed

        Notes:
        - Adds Cache-Control: no-store to reduce the chance of cached/stale responses.
        - Treats any request/parse exception as "no update".
        """
        try:
            with httpx.Client(timeout=self._cfg.timeout_sec) as client:
                r = client.get(self._cfg.ui_url, headers={"Cache-Control": "no-store"})
                r.raise_for_status()
                data = r.json()
        except Exception:
            return None

        # Validate shape: expecting an object with a boolean field.
        raw = data.get("show_tile_numbers") if isinstance(data, dict) else None
        if not isinstance(raw, bool):
            return None

        # Compare-and-swap under lock; only return when the value is new.
        with self._lock:
            if self._last_show_tile_numbers is None:
                self._last_show_tile_numbers = raw
                return raw
            if raw == self._last_show_tile_numbers:
                return None
            self._last_show_tile_numbers = raw
            return raw

    def reset(self) -> None:
        """
        Clear cached state so the next successful poll returns the fetched value (even if unchanged).
        """
        with self._lock:
            self._last_show_tile_numbers = None
