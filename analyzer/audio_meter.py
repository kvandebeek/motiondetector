"""Best-effort stereo audio level meter using SoundCard loopback capture."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Optional

import numpy as np

try:
    import soundcard as sc
except Exception:  # pragma: no cover - optional dependency at runtime
    sc = None


@dataclass(frozen=True)
class AudioLevel:
    available: bool
    left: float
    right: float
    reason: str


class AudioMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._available = False
        self._left = 0.0
        self._right = 0.0
        self._reason = "not_initialized"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-meter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def get_level(self) -> AudioLevel:
        with self._lock:
            return AudioLevel(self._available, self._left, self._right, self._reason)

    def _set(self, *, available: bool, left: float, right: float, reason: str) -> None:
        with self._lock:
            self._available = bool(available)
            self._left = float(max(0.0, min(100.0, left)))
            self._right = float(max(0.0, min(100.0, right)))
            self._reason = str(reason)

    def _run(self) -> None:
        if sc is None:
            self._set(available=False, left=0.0, right=0.0, reason="soundcard_module_missing")
            return

        try:
            speaker = sc.default_speaker()
            if speaker is None:
                self._set(available=False, left=0.0, right=0.0, reason="no_sound_hardware")
                return
        except Exception as e:
            self._set(available=False, left=0.0, right=0.0, reason=f"speaker_probe_failed:{e}")
            return

        try:
            with speaker.recorder(samplerate=48_000, channels=2, blocksize=1024) as rec:
                self._set(available=True, left=0.0, right=0.0, reason="ok")
                while not self._stop.is_set():
                    data = rec.record(numframes=1024)
                    if data.size == 0:
                        continue
                    l = float(np.sqrt(np.mean(np.square(data[:, 0])))) if data.shape[1] >= 1 else 0.0
                    r = float(np.sqrt(np.mean(np.square(data[:, 1])))) if data.shape[1] >= 2 else l
                    self._set(available=True, left=l * 100.0, right=r * 100.0, reason="ok")
        except Exception as e:
            self._set(available=False, left=0.0, right=0.0, reason=f"capture_failed:{e}")
            while not self._stop.is_set():
                time.sleep(0.5)
