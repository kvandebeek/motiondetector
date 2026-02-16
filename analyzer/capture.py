from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Dict, Literal

import mss
import numpy as np


Backend = Literal["MSS"]


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int


class ScreenCapturer:
    """
    Capture frames from a region.

    For MSS on Windows, MSS uses thread-local resources.
    To avoid: "_thread._local object has no attribute 'srcdc'"
    we keep an mss.mss() instance PER THREAD (thread-local).
    """

    def __init__(self, backend: str) -> None:
        b = backend.strip().upper()
        if b != "MSS":
            raise ValueError(f"Unsupported capture backend: {backend!r} (expected 'MSS')")
        self._backend: Backend = "MSS"
        self._tls = threading.local()

    def grab(self, region: Region) -> np.ndarray:
        """
        Returns BGRA uint8 frame as np.ndarray shape (H, W, 4).
        Must be called from the thread that will process the frame.
        """
        if self._backend != "MSS":
            raise RuntimeError("Unsupported backend configuration")

        sct = getattr(self._tls, "sct", None)
        if sct is None:
            # Create inside current thread
            self._tls.sct = mss.mss()
            sct = self._tls.sct

        mon: Dict[str, int] = {
            "left": int(region.x),
            "top": int(region.y),
            "width": int(region.width),
            "height": int(region.height),
        }
        shot = sct.grab(mon)
        return np.asarray(shot)

    def close_thread_resources(self) -> None:
        """
        Optional: call from a worker thread before it exits to close MSS resources.
        """
        sct = getattr(self._tls, "sct", None)
        if sct is not None:
            try:
                sct.close()
            finally:
                self._tls.sct = None
