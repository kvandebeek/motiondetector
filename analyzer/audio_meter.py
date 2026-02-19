"""Best-effort stereo audio level meter using Windows loopback capture via PyAudioWPatch."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Optional

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except Exception:  # pragma: no cover - optional dependency at runtime
    pyaudio = None


@dataclass(frozen=True)
class AudioLevel:
    available: bool
    left: float
    right: float
    reason: str


class AudioMeter:
    def __init__(
        self,
        *,
        enabled: bool = True,
        device_substr: str = "",
        device_index: Optional[int] = None,
        samplerate: int = 48_000,
        channels: int = 2,
        block_ms: int = 250,
    ) -> None:
        self._enabled = bool(enabled)
        self._device_substr = str(device_substr or "").strip().lower()
        self._device_index = int(device_index) if device_index is not None else None
        self._samplerate = max(1, int(samplerate))
        self._channels = max(1, int(channels))
        self._block_ms = max(1, int(block_ms))

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

    @staticmethod
    def _rms_value(x: np.ndarray) -> float:
        y = x.astype(np.float32)
        return float(np.sqrt(np.mean(y * y))) if y.size else 0.0

    def _pick_loopback_device(self, pa: "pyaudio.PyAudio") -> int:
        if self._device_index is not None:
            return int(self._device_index)

        preferred: Optional[int] = None
        fallback: Optional[int] = None

        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = str(info.get("name", ""))
            max_in = int(info.get("maxInputChannels", 0))
            if max_in <= 0:
                continue

            lower_name = name.lower()
            if self._device_substr and self._device_substr in lower_name:
                return i

            if "loopback" in lower_name and preferred is None:
                preferred = i
            if fallback is None:
                fallback = i

        if preferred is not None:
            return preferred
        if fallback is not None:
            return fallback
        raise RuntimeError("no_loopback_input_device")

    def _run(self) -> None:
        if not self._enabled:
            self._set(available=False, left=0.0, right=0.0, reason="disabled")
            return

        if pyaudio is None:
            self._set(available=False, left=0.0, right=0.0, reason="pyaudiowpatch_module_missing")
            return

        pa = pyaudio.PyAudio()
        stream = None
        try:
            dev_idx = self._pick_loopback_device(pa)
            dev = pa.get_device_info_by_index(dev_idx)
            max_in = int(dev.get("maxInputChannels", 0))
            if max_in <= 0:
                raise RuntimeError(f"device_not_input:{dev_idx}")

            channels = min(self._channels, max_in)
            frames = int(round((self._block_ms / 1000.0) * self._samplerate))
            frames = max(256, frames)

            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=int(self._samplerate),
                input=True,
                input_device_index=dev_idx,
                frames_per_buffer=frames,
            )

            self._set(available=True, left=0.0, right=0.0, reason="ok")

            while not self._stop.is_set():
                raw = stream.read(frames, exception_on_overflow=False)
                arr = np.frombuffer(raw, dtype=np.float32)
                if arr.size == 0:
                    continue

                if channels > 1:
                    arr2 = arr.reshape((-1, channels))
                    left = self._rms_value(arr2[:, 0])
                    right = self._rms_value(arr2[:, 1]) if channels > 1 else left
                else:
                    left = self._rms_value(arr)
                    right = left

                self._set(available=True, left=left * 100.0, right=right * 100.0, reason="ok")
        except Exception as e:
            self._set(available=False, left=0.0, right=0.0, reason=f"capture_failed:{e}")
            while not self._stop.is_set():
                time.sleep(0.5)
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            pa.terminate()
