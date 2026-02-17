# ui/tiles_sync.py
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Optional, Set
from urllib.request import Request, urlopen


def http_get_json(url: str, *, timeout_sec: float) -> Optional[dict]:
    """
    Perform a simple HTTP GET and parse the response body as JSON.

    Returns:
        - dict on success (only if the decoded JSON top-level is an object)
        - None on any error (network, timeout, decode, parse, non-dict JSON)

    Notes:
    - Uses urllib from the stdlib to avoid adding runtime deps in the UI layer.
    - Decodes as UTF-8 with replacement to tolerate minor encoding issues.
    """
    try:
        req = Request(url=url, method="GET")
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def http_put_json(url: str, *, payload: dict, timeout_sec: float) -> Optional[dict]:
    """
    Perform an HTTP PUT with a JSON request body and parse the JSON response.

    Returns:
        - dict on success (only if the decoded JSON top-level is an object)
        - None on any error (network, timeout, decode, parse, non-dict JSON)

    Contract expectation:
    - The server accepts a JSON body and responds with JSON (typically echoing/confirming state).
    """
    try:
        body = json.dumps(payload).encode("utf-8")
        req = Request(url=url, data=body, method="PUT", headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


@dataclass
class TilesSyncConfig:
    """
    Configuration for syncing tile enable/disable state from/to a server.

    - tiles_url: endpoint providing and accepting tile state.
    - timeout_sec: per-request timeout for GET/PUT.
    - grid_rows/grid_cols: used to validate tile indices and ignore out-of-range values.
    """
    tiles_url: str
    timeout_sec: float
    grid_rows: int
    grid_cols: int


class TilesSync:
    """
    Synchronize disabled-tile state with a remote server endpoint.

    Server contract (expected):
    - GET  tiles_url -> {"disabled_tiles": [0, 5, 8, ...]}
    - PUT  tiles_url with {"disabled_tiles": [...]} -> responds with same shape (authoritative)

    Local behavior:
    - poll(): fetches server state and updates local disabled_tiles; returns True if changed.
    - toggle(idx0): optimistically flips a single tile and sends updated set via PUT.

    Concurrency model:
    - This object is used from the UI thread.
    - _inflight is a simple guard to prevent re-entrancy (e.g., spam-click while a PUT is ongoing).
    """
    def __init__(self, cfg: TilesSyncConfig) -> None:
        self._cfg = cfg
        self._disabled_tiles: Set[int] = set()

        # True while a toggle() PUT is in progress.
        # poll() will not run while inflight to avoid overwriting optimistic state mid-request.
        self._inflight = False

    @property
    def disabled_tiles(self) -> Set[int]:
        """
        Current disabled tile indices (0-based).

        Returned as a copy so callers cannot mutate internal state inadvertently.
        """
        return set(self._disabled_tiles)

    @property
    def inflight(self) -> bool:
        """
        Whether a state-changing PUT request is currently in progress.
        """
        return self._inflight

    def poll(self) -> bool:
        """
        Fetch server state and update local disabled tile set.

        Returns:
            bool: True if the local set changed as a result of the poll.

        Behavior:
        - No-op if a PUT is inflight (keeps optimistic UI stable during toggle).
        - Validates that disabled tile indices are within [0, rows*cols).
        - Ignores invalid data or network errors.
        """
        if self._inflight:
            return False

        data = http_get_json(self._cfg.tiles_url, timeout_sec=self._cfg.timeout_sec)
        if not data:
            return False

        raw = data.get("disabled_tiles")
        if not isinstance(raw, list):
            return False

        n = self._cfg.grid_rows * self._cfg.grid_cols
        new_set: Set[int] = {int(v) for v in raw if isinstance(v, int) and 0 <= int(v) < n}

        changed = new_set != self._disabled_tiles
        self._disabled_tiles = new_set
        return changed

    def toggle(self, idx0: int) -> bool:
        """
        Toggle a tile (0-based index) disabled/enabled state and sync to the server.

        Returns:
            bool: True if the request was accepted for processing (even if server is unreachable),
                  False if rejected locally (inflight or invalid idx).

        Implementation details:
        - Applies optimistic update first to keep UI responsive.
        - Performs a blocking PUT (urllib) and then, if response is valid, adopts server state
          as authoritative.
        - Always clears _inflight in a finally block.
        """
        if self._inflight:
            return False

        n = self._cfg.grid_rows * self._cfg.grid_cols
        if idx0 < 0 or idx0 >= n:
            return False

        next_set = set(self._disabled_tiles)
        if idx0 in next_set:
            next_set.remove(idx0)
        else:
            next_set.add(idx0)

        # Optimistic UI update: reflects the user's click immediately.
        self._disabled_tiles = next_set

        self._inflight = True
        try:
            res = http_put_json(
                self._cfg.tiles_url,
                payload={"disabled_tiles": sorted(next_set)},
                timeout_sec=self._cfg.timeout_sec,
            )

            # If the server responds with a valid list, treat it as authoritative.
            if isinstance(res, dict) and isinstance(res.get("disabled_tiles"), list):
                raw = res.get("disabled_tiles")
                self._disabled_tiles = {int(v) for v in raw if isinstance(v, int) and 0 <= int(v) < n}
        finally:
            self._inflight = False

        return True
