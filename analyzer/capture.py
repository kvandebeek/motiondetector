from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Dict, Literal

import mss
import numpy as np


# Narrow the allowed backend values at the type level.
# This makes it harder to accidentally pass unsupported backends around the codebase.
Backend = Literal["MSS"]


@dataclass(frozen=True)
class Region:
    """
    Immutable rectangle describing a screen area in absolute desktop coordinates.

    Coordinates are expected to be in pixels:
    - x, y: top-left corner
    - width, height: size of the region
    """
    x: int
    y: int
    width: int
    height: int


class ScreenCapturer:
    """
    Capture frames from a screen region.

    Why thread-local MSS instances:
    - On Windows, MSS internally stores per-thread handles/resources (e.g., GDI/DC handles).
    - Re-using one mss instance across multiple threads can lead to errors such as:
        "_thread._local object has no attribute 'srcdc'"
    - To keep capture stable, we create and cache an `mss.mss()` instance *per thread* using
      `threading.local()`.

    Output format:
    - `grab()` returns a NumPy array with dtype=uint8 and shape (H, W, 4) in **BGRA** order.
      (Alpha channel is included.)
    """

    def __init__(self, backend: str) -> None:
        """
        Create a capturer with the requested backend.

        Notes:
        - `backend` is accepted as `str` to align with config loading (JSON/env/CLI).
        - We normalize to upper-case and validate immediately for fail-fast behavior.
        """
        b = backend.strip().upper()
        if b != "MSS":
            raise ValueError(f"Unsupported capture backend: {backend!r} (expected 'MSS')")

        # Store as a constrained literal to keep the rest of the class type-safe.
        self._backend: Backend = "MSS"

        # Thread-local storage:
        # Each thread gets its own `.sct` attribute (the MSS instance) when first used.
        self._tls = threading.local()

    def grab(self, region: Region) -> np.ndarray:
        """
        Capture a single frame for the given region.

        Contract:
        - Returns: BGRA uint8 frame as np.ndarray with shape (H, W, 4).
        - Threading: Must be called from the thread that will perform captures.
          The first `grab()` call in a thread will lazily create that thread's MSS instance.

        Design choices:
        - Lazy initialization avoids creating MSS resources for threads that never capture.
        - Returning BGRA (not converting to BGR/RGB) avoids extra CPU work; callers can convert
          later only if needed.
        """
        # Defensive check: in case future refactors allow `_backend` changes.
        if self._backend != "MSS":
            raise RuntimeError("Unsupported backend configuration")

        # Fetch or create a per-thread MSS instance.
        sct = getattr(self._tls, "sct", None)
        if sct is None:
            # IMPORTANT: create inside the current thread so MSS binds resources correctly.
            self._tls.sct = mss.mss()
            sct = self._tls.sct

        # MSS expects a dict with these keys.
        # We cast to int to guarantee correct types even if Region was constructed from floats.
        mon: Dict[str, int] = {
            "left": int(region.x),
            "top": int(region.y),
            "width": int(region.width),
            "height": int(region.height),
        }

        # `grab()` returns an MSS "ScreenShot"-like object; `np.asarray` converts without
        # copying when possible, yielding a (H, W, 4) uint8 array (BGRA).
        shot = sct.grab(mon)
        return np.asarray(shot)

    def close_thread_resources(self) -> None:
        """
        Close MSS resources owned by the *current thread*.

        When to call:
        - If you create worker threads that capture and then terminate, call this from each
          worker thread right before exiting to clean up native resources promptly.

        Notes:
        - This only affects the calling thread's thread-local MSS instance.
        - Safe to call multiple times; after closing we clear the thread-local reference.
        """
        sct = getattr(self._tls, "sct", None)
        if sct is not None:
            try:
                sct.close()
            finally:
                # Ensure we don't keep a closed instance around.
                self._tls.sct = None
