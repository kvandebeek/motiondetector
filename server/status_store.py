# File commentary: server/status_store.py - This file holds logic used by the motion detector project.
"""Thread-safe in-memory state store shared by analyzer, UI, and HTTP routes.

The store is intentionally the single source of truth for runtime status payloads,
rolling history, UI toggles, and disabled tile indices. Keeping this logic centralized
reduces cross-thread coupling and makes server routes thin/deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import threading
import time
from typing import Any, Deque, Dict, List


# JSON-ish payload type used throughout the server/analyzer boundary.
JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class StatusSample:
    """
    One history entry.

    We store the timestamp separately from the payload to make trimming reliable even if:
    - payload timestamps are missing
    - payload timestamps are non-numeric
    - payload timestamps are rewritten by callers
    """
    ts: float
    payload: JsonDict


class StatusStore:
    """
    Thread-safe store for application state shared across threads.

    Responsibilities:
      - Latest status payload (produced by analyzer/monitor loop)
      - Rolling history of payloads (for charting and debugging)
      - UI settings (server-side source of truth for UI toggles)
      - Disabled tile mask (server-side source of truth; consumed by analyzer + web UI)
      - Quit signalling (web endpoint requests shutdown; main loop polls this)

    Threading model:
      - The monitor loop thread calls `set_latest(...)` frequently.
      - The web server thread(s) call getters/setters for UI settings and tile mask.
      - All state is protected by a single lock; operations are small and bounded.

    Schema policy:
      - `get_payload()` returns the public schema:
          * injects UI settings into the payload
          * forces disabled tiles to None in video.tiles
          * ensures tiles list matches grid size and is JSON-safe
      - The store accepts arbitrary payload dicts but normalizes at read time.
    """

    def __init__(
        self,
        history_seconds: float,
        *,
        grid_rows: int,
        grid_cols: int,
        show_tile_numbers: bool = True,
        show_overlay_state: bool = False,
        region_x: int = 0,
        region_y: int = 0,
        region_width: int = 640,
        region_height: int = 480,
        monitors: list[dict[str, int]] | None = None,
        current_monitor_id: int = 0,
    ) -> None:
        self._history_seconds = float(history_seconds)
        self._lock = threading.Lock()

        # Store-level grid defaults are used when incoming payloads omit grid metadata.
        self._grid_rows = max(1, int(grid_rows))
        self._grid_cols = max(1, int(grid_cols))

        # Start with a well-formed payload so /status can be called before the analyzer runs.
        self._latest: JsonDict = self._default_payload(
            reason="not_initialized",
            grid_rows=self._grid_rows,
            grid_cols=self._grid_cols,
            show_tile_numbers=bool(show_tile_numbers),
        )

        # Rolling window of samples for the chart/history endpoint.
        self._history: Deque[StatusSample] = deque()

        # Shutdown request flag set by /quit endpoint, read by main app loop.
        self._quit_requested = False

        # Single source of truth for tile-number visibility across overlay + heatmap.
        self._show_tile_numbers = bool(show_tile_numbers)
        self._show_overlay_state = bool(show_overlay_state)

        self._region_x = int(region_x)
        self._region_y = int(region_y)
        self._region_width = max(1, int(region_width))
        self._region_height = max(1, int(region_height))

        self._monitors = [dict(m) for m in (monitors or []) if isinstance(m, dict)]
        self._current_monitor_id = int(current_monitor_id)

        # 0-based tile indices disabled via web UI clicks.
        self._disabled_tiles: list[int] = []

        # Recent quality events emitted by the analyzer.
        self._quality_events: Deque[JsonDict] = deque(maxlen=500)

    # ----------------------------
    # Status payload + history
    # ----------------------------

    def set_latest(self, payload: JsonDict) -> None:
        """
        Store a new status payload and append it to history.

        Notes:
        - Timestamp is taken from payload["timestamp"] when present; otherwise `time.time()`.
        - History is trimmed on each insert to keep memory bounded.
        """
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        ts_raw = payload.get("timestamp", time.time())
        try:
            ts = float(ts_raw)
        except (TypeError, ValueError):
            ts = time.time()
        with self._lock:
            self._latest = payload
            self._history.append(StatusSample(ts=ts, payload=payload))
            self._trim_locked(now=ts)

    def get_latest(self) -> JsonDict:
        """
        Return a shallow copy of the most recently stored raw payload.

        Callers should prefer `get_payload()` for the normalized public schema.
        """
        with self._lock:
            return dict(self._latest)

    def get_payload(self) -> JsonDict:
        """
        Return the latest payload in the public schema, with server-side state injected.

        Normalization performed:
        - Ensures `video.grid` exists and is valid (fallback to store defaults).
        - Ensures `video.tiles` is a list[float|None] of length rows*cols.
        - Adds `video.tiles_indexed` as [{tile, value}] in row-major order.
        - Applies the disabled tile mask by forcing disabled indices to None.
        - Ensures `errors` is always a list.
        - Injects `ui` settings so clients have a single polling source.

        Compatibility:
        - This method intentionally does not add legacy back-compat keys.
        """
        payload = self.get_latest()

        # Extract/normalize video section.
        video_raw = payload.get("video")
        video: Dict[str, Any] = dict(video_raw) if isinstance(video_raw, dict) else {}

        disabled = self.get_disabled_tiles()

        # Ensure grid exists (fallback to store config).
        grid_raw = video.get("grid")
        if isinstance(grid_raw, dict):
            rows = int(grid_raw.get("rows", self._grid_rows))
            cols = int(grid_raw.get("cols", self._grid_cols))
        else:
            rows = self._grid_rows
            cols = self._grid_cols

        rows = max(1, rows)
        cols = max(1, cols)
        n = rows * cols

        # Ensure tiles is list[float|None] length n.
        # Non-numeric values are treated as disabled/invalid (None).
        tiles_raw = video.get("tiles")
        tiles_out: list[float | None] = []

        if isinstance(tiles_raw, list):
            for v in tiles_raw[:n]:
                if v is None:
                    tiles_out.append(None)
                elif isinstance(v, bool):
                    # Avoid bool-as-int leakage (True -> 1).
                    tiles_out.append(None)
                elif isinstance(v, (int, float)):
                    tiles_out.append(float(v))
                else:
                    tiles_out.append(None)

        # Pad if short (default to 0), truncate if long.
        if len(tiles_out) < n:
            tiles_out.extend([0.0] * (n - len(tiles_out)))
        if len(tiles_out) > n:
            tiles_out = tiles_out[:n]

        # Apply disabled mask: force disabled indices to None.
        # Store ensures indices are non-negative; here we also clamp to grid bounds.
        store_disabled_set = set(i for i in disabled if 0 <= int(i) < n)
        for i in store_disabled_set:
            tiles_out[int(i)] = None

        disabled_set = {i for i, v in enumerate(tiles_out) if v is None}
        tiles_indexed = [
            {"tile": i, "value": "disabled" if v is None else float(v)}
            for i, v in enumerate(tiles_out)
        ]

        # Build clean video section that downstream clients can rely on.
        clean_video: Dict[str, Any] = {
            "state": video.get("state", "ERROR"),
            "confidence": float(video.get("confidence", 0.0)),
            "motion_mean": float(video.get("motion_mean", 0.0)),
            "motion_instant_mean": float(video.get("motion_instant_mean", 0.0)),
            "motion_instant_top1": float(video.get("motion_instant_top1", 0.0)),
            "motion_instant_activity": float(video.get("motion_instant_activity", 0.0)),
            "grid": {"rows": rows, "cols": cols},
            "tiles": tiles_out,
            "tiles_indexed": tiles_indexed,
            "disabled_tiles": sorted(disabled_set),
            "stale": bool(video.get("stale", False)),
            "stale_age_sec": float(video.get("stale_age_sec", 0.0)),
            "blockiness": self._normalize_blockiness(video.get("blockiness")),
            "quality": self._normalize_quality(video.get("quality")),
        }

        # Copy original payload and replace sections with normalized versions.
        out: JsonDict = dict(payload)
        out["video"] = clean_video

        # Preserve top-level objects if present (shallow copies for safety).
        capture_raw = out.get("capture")
        if isinstance(capture_raw, dict):
            out["capture"] = dict(capture_raw)

        overall_raw = out.get("overall")
        if isinstance(overall_raw, dict):
            out["overall"] = dict(overall_raw)

        region_raw = out.get("region")
        if isinstance(region_raw, dict):
            out["region"] = dict(region_raw)

        # Ensure errors is always a list.
        errors_raw = out.get("errors")
        if isinstance(errors_raw, list):
            out["errors"] = list(errors_raw)
        else:
            out["errors"] = []

        # Inject UI settings so the web UI can render consistently across clients.
        out["ui"] = self.get_ui_settings()

        return out

    def get_history(self) -> List[JsonDict]:
        """
        Return the raw history payloads within the configured rolling window.

        Notes:
        - Trims the deque under lock before returning.
        - Returns the stored payloads as-is (no normalization). Use get_payload_history()
          if you need injected UI settings for each entry.
        """
        now = time.time()
        with self._lock:
            self._trim_locked(now=now)
            return [s.payload for s in list(self._history)]

    def get_payload_history(self) -> List[JsonDict]:
        """
        Return history payloads suitable for the public API.

        Current behavior:
        - Injects the current UI settings into each returned payload.

        Note:
        - UI settings are not historically versioned; history shows the current UI state,
          not the state at the time of each sample.
        """
        raw = self.get_history()
        ui = self.get_ui_settings()
        out: List[JsonDict] = []
        for p in raw:
            pp: JsonDict = dict(p)
            pp["ui"] = ui
            out.append(pp)
        return out

    def get_history_seconds(self) -> float:
        """
        Return the configured rolling history retention window in seconds.
        """
        with self._lock:
            return float(self._history_seconds)

    # ----------------------------
    # UI settings
    # ----------------------------

    def set_show_tile_numbers(self, enabled: bool) -> None:
        """
        Persist the tile-number overlay toggle.

        This is used by:
        - the web UI (heatmap numbers)
        - any other UI surfaces that want a shared toggle (e.g., overlay window)
        """
        with self._lock:
            self._show_tile_numbers = bool(enabled)

    def get_show_tile_numbers(self) -> bool:
        """
        Read the current tile-number overlay toggle.
        """
        with self._lock:
            return bool(self._show_tile_numbers)

    def get_ui_settings(self) -> JsonDict:
        """
        Return the UI settings object exposed via /ui and injected into /status.

        Kept as a dict to allow future settings without changing the route surface.
        """
        with self._lock:
            return {
                "show_tile_numbers": bool(self._show_tile_numbers),
                "show_overlay_state": bool(self._show_overlay_state),
                "grid_rows": int(self._grid_rows),
                "grid_cols": int(self._grid_cols),
                "region_x": int(self._region_x),
                "region_y": int(self._region_y),
                "region_width": int(self._region_width),
                "region_height": int(self._region_height),
                "current_state": self._current_state_locked(),
                "monitors": [dict(m) for m in self._monitors],
                "current_monitor_id": int(self._current_monitor_id),
            }

    def set_show_overlay_state(self, enabled: bool) -> None:
        """Update show overlay state in this component's state."""
        with self._lock:
            self._show_overlay_state = bool(enabled)

    def set_region(self, *, x: int, y: int, width: int, height: int) -> None:
        """Update region in this component's state."""
        with self._lock:
            self._region_x = int(x)
            self._region_y = int(y)
            self._region_width = max(1, int(width))
            self._region_height = max(1, int(height))

            cx = self._region_x + max(1, self._region_width) // 2
            cy = self._region_y + max(1, self._region_height) // 2
            for m in self._monitors:
                mid = int(m.get("id", 0))
                if mid <= 0:
                    continue
                left = int(m.get("left", 0))
                top = int(m.get("top", 0))
                width_m = int(m.get("width", 0))
                height_m = int(m.get("height", 0))
                if left <= cx < left + width_m and top <= cy < top + height_m:
                    self._current_monitor_id = mid
                    break

    def get_region(self) -> tuple[int, int, int, int]:
        """Return the current region value for callers."""
        with self._lock:
            return (int(self._region_x), int(self._region_y), int(self._region_width), int(self._region_height))

    def set_monitors(self, monitors: list[dict[str, int]]) -> None:
        """Update monitors in this component's state."""
        with self._lock:
            self._monitors = [dict(m) for m in monitors if isinstance(m, dict)]

    def set_current_monitor_id(self, monitor_id: int) -> None:
        """Update current monitor id in this component's state."""
        with self._lock:
            self._current_monitor_id = int(monitor_id)

    def _current_state_locked(self) -> str:
        """Handle current state locked for this module."""
        payload = self._latest
        if isinstance(payload, dict):
            video = payload.get("video")
            if isinstance(video, dict):
                state = video.get("state")
                if isinstance(state, str) and state.strip():
                    return state.strip()
            overall = payload.get("overall")
            if isinstance(overall, dict):
                state = overall.get("state")
                if isinstance(state, str) and state.strip():
                    return state.strip()
        return "UNKNOWN"

    def set_grid(self, *, rows: int, cols: int) -> None:
        """Update grid in this component's state."""
        with self._lock:
            self._grid_rows = max(1, int(rows))
            self._grid_cols = max(1, int(cols))

    def get_grid(self) -> tuple[int, int]:
        """Return the current grid value for callers."""
        with self._lock:
            return int(self._grid_rows), int(self._grid_cols)

    # ----------------------------
    # Tile mask (disabled tiles)
    # ----------------------------

    def set_disabled_tiles(self, disabled_tiles: List[int]) -> None:
        """
        Replace the disabled tile list.

        Validation:
        - Requires a list input.
        - Keeps only non-negative integers.
        - Deduplicates and sorts for deterministic responses.

        Note:
        - Range validation (0..N-1) is applied in get_payload() because N depends
          on the current grid size.
        """
        if not isinstance(disabled_tiles, list):
            raise TypeError("disabled_tiles must be a list[int]")
        cleaned = sorted({int(i) for i in disabled_tiles if isinstance(i, int) and i >= 0})
        with self._lock:
            self._disabled_tiles = cleaned

    def get_disabled_tiles(self) -> List[int]:
        """
        Return the current disabled tile indices (0-based).
        """
        with self._lock:
            return list(self._disabled_tiles)

    # ----------------------------
    # Quality events
    # ----------------------------

    def add_quality_event(self, event: JsonDict) -> None:
        """Append one quality event record."""
        if not isinstance(event, dict):
            return
        with self._lock:
            self._quality_events.append(dict(event))

    def get_quality_events(self) -> List[JsonDict]:
        """Return quality events in chronological order."""
        with self._lock:
            return [dict(e) for e in list(self._quality_events)]

    # ----------------------------
    # Quit signalling
    # ----------------------------

    def request_quit(self) -> None:
        """
        Request a clean application shutdown.

        The web server sets this flag; the main application should poll it and coordinate:
        - stopping monitor loop
        - closing UI
        - shutting down server thread (process exit)
        """
        with self._lock:
            self._quit_requested = True

    def quit_requested(self) -> bool:
        """
        Check whether a shutdown has been requested.
        """
        with self._lock:
            return self._quit_requested

    # ----------------------------
    # Internals
    # ----------------------------

    def _trim_locked(self, now: float) -> None:
        """
        Trim history deque to the configured rolling time window.

        Must be called with self._lock held.
        """
        cutoff = float(now) - self._history_seconds
        while self._history and self._history[0].ts < cutoff:
            self._history.popleft()

    @staticmethod
    def _normalize_blockiness(raw: Any) -> Dict[str, Any]:
        """Return blockiness section in a stable shape."""
        if isinstance(raw, dict):
            enabled = bool(raw.get("enabled", False))
            block_sizes_raw = raw.get("block_sizes", [8, 16])
            block_sizes = [int(v) for v in block_sizes_raw if isinstance(v, (int, float)) and int(v) > 1]
            if not block_sizes:
                block_sizes = [8, 16]
            score_by_raw = raw.get("score_by_block") if isinstance(raw.get("score_by_block"), dict) else {}
            score_by: Dict[str, float | None] = {}
            for b in block_sizes:
                val = score_by_raw.get(str(b))
                score_by[str(b)] = float(val) if isinstance(val, (int, float)) else None

            score_raw = raw.get("score")
            score_ema_raw = raw.get("score_ema")
            sample_raw = raw.get("sample_every_frames", 25)
            downscale_raw = raw.get("downscale_width", 640)
            sample = int(sample_raw) if isinstance(sample_raw, (int, float)) else 25
            downscale = int(downscale_raw) if isinstance(downscale_raw, (int, float)) else 640
            return {
                "enabled": enabled,
                "block_sizes": block_sizes,
                "score": float(score_raw) if isinstance(score_raw, (int, float)) else None,
                "score_ema": float(score_ema_raw) if isinstance(score_ema_raw, (int, float)) else None,
                "score_by_block": score_by,
                "sample_every_frames": max(1, sample),
                "downscale_width": max(1, downscale),
            }

        return {
            "enabled": False,
            "block_sizes": [8, 16],
            "score": None,
            "score_ema": None,
            "score_by_block": {"8": None, "16": None},
            "sample_every_frames": 25,
            "downscale_width": 640,
        }

    @staticmethod
    def _normalize_quality(raw: Any) -> Dict[str, Any]:
        """Return quality section in a stable shape."""
        thresholds_default = {
            "ringing": 0.65,
            "banding": 0.65,
            "cadence_jitter": 0.65,
            "duplicate_ratio": 0.65,
            "motion_blur": 0.65,
        }
        if isinstance(raw, dict):
            thresholds_raw = raw.get("thresholds") if isinstance(raw.get("thresholds"), dict) else {}
            thresholds = {
                k: float(thresholds_raw.get(k, v)) if isinstance(thresholds_raw.get(k, v), (int, float)) else float(v)
                for k, v in thresholds_default.items()
            }
            active_raw = raw.get("active_problems")
            active = [str(v) for v in active_raw if isinstance(v, str)] if isinstance(active_raw, list) else []
            return {
                "enabled": bool(raw.get("enabled", False)),
                "sample_every_frames": max(1, int(raw.get("sample_every_frames", 3))) if isinstance(raw.get("sample_every_frames", 3), (int, float)) else 3,
                "thresholds": thresholds,
                "ringing": float(raw.get("ringing", 0.0)) if isinstance(raw.get("ringing", 0.0), (int, float)) else 0.0,
                "banding": float(raw.get("banding", 0.0)) if isinstance(raw.get("banding", 0.0), (int, float)) else 0.0,
                "cadence_jitter": float(raw.get("cadence_jitter", 0.0)) if isinstance(raw.get("cadence_jitter", 0.0), (int, float)) else 0.0,
                "duplicate_ratio": float(raw.get("duplicate_ratio", 0.0)) if isinstance(raw.get("duplicate_ratio", 0.0), (int, float)) else 0.0,
                "motion_blur": float(raw.get("motion_blur", 0.0)) if isinstance(raw.get("motion_blur", 0.0), (int, float)) else 0.0,
                "active_problems": active,
            }

        return {
            "enabled": False,
            "sample_every_frames": 3,
            "thresholds": thresholds_default,
            "ringing": 0.0,
            "banding": 0.0,
            "cadence_jitter": 0.0,
            "duplicate_ratio": 0.0,
            "motion_blur": 0.0,
            "active_problems": [],
        }

    @staticmethod
    def _default_payload(*, reason: str, grid_rows: int, grid_cols: int, show_tile_numbers: bool) -> JsonDict:
        """
        Create a well-formed “error/empty” payload used before the analyzer produces data.

        This ensures:
        - /status always returns a schema-correct payload
        - the UI can render immediately (with placeholders) instead of handling missing keys
        """
        now = time.time()

        rows = max(1, int(grid_rows))
        cols = max(1, int(grid_cols))
        n = rows * cols

        return {
            "timestamp": float(now),
            "capture": {"state": "ERROR", "reason": str(reason), "backend": "UNKNOWN"},
            "video": {
                "state": "ERROR",
                "confidence": 0.0,
                "motion_mean": 0.0,
                "motion_instant_mean": 0.0,
                "motion_instant_top1": 0.0,
                "motion_instant_activity": 0.0,
                "grid": {"rows": rows, "cols": cols},
                "tiles": [0.0] * n,
                "tiles_indexed": [{"tile": i, "value": 0.0} for i in range(n)],
                "disabled_tiles": [],
                "stale": True,
                "stale_age_sec": 0.0,
                "blockiness": {
                    "enabled": False,
                    "block_sizes": [8, 16],
                    "score": None,
                    "score_ema": None,
                    "score_by_block": {"8": None, "16": None},
                    "sample_every_frames": 25,
                    "downscale_width": 640,
                },
                "quality": {
                    "enabled": False,
                    "sample_every_frames": 3,
                    "thresholds": {
                        "ringing": 0.65,
                        "banding": 0.65,
                        "cadence_jitter": 0.65,
                        "duplicate_ratio": 0.65,
                        "motion_blur": 0.65,
                    },
                    "ringing": 0.0,
                    "banding": 0.0,
                    "cadence_jitter": 0.0,
                    "duplicate_ratio": 0.0,
                    "motion_blur": 0.0,
                    "active_problems": [],
                },
            },
            "audio": {"state": "ERROR", "reason": "not_initialized", "level": 0.0, "rms": 0.0, "peak": 0.0, "baseline": 0.0, "threshold": 0.0, "detected": False, "timestamp": float(now)},
            "ui": {"show_tile_numbers": bool(show_tile_numbers)},
            "audio": {"available": False, "left": 0.0, "right": 0.0, "detected": False, "reason": "not_initialized"},
            "overall": {"state": "NOT_OK", "reasons": [str(reason)]},
            "errors": [str(reason)],
            "region": {"x": 0, "y": 0, "width": 0, "height": 0},
        }
