"""Best-effort audio level meter using Windows loopback (PyAudioWPatch) or WASAPI sessions (pycaw)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Iterable, Optional, Set

import numpy as np
from analyzer.audio_devices import list_audio_devices, resolve_device_index

try:
    import pyaudiowpatch as pyaudio
except Exception:  # pragma: no cover - optional dependency at runtime
    pyaudio = None

try:
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
except Exception:  # pragma: no cover - optional dependency at runtime
    AudioUtilities = None
    IAudioMeterInformation = None


@dataclass(frozen=True)
class AudioLevel:
    available: bool
    left: float
    right: float
    detected: bool
    reason: str


class AudioMeter:
    def __init__(
        self,
        *,
        enabled: bool = True,
        backend: str = "pyaudiowpatch",
        device_substr: str = "",
        device_index: Optional[int] = None,
        device_id: str = "",
        samplerate: int = 48_000,
        channels: int = 2,
        block_ms: int = 250,
        process_names: Optional[Iterable[str]] = None,
        on_threshold: float = 0.01,
        off_threshold: float = 0.005,
        hold_ms: int = 300,
        smooth_samples: int = 3,
    ) -> None:
        self._enabled = bool(enabled)
        self._backend = str(backend or "pyaudiowpatch").strip().lower()
        self._device_substr = str(device_substr or "").strip().lower()
        self._device_index = int(device_index) if device_index is not None else None
        self._device_id = str(device_id or "").strip()
        self._samplerate = max(1, int(samplerate))
        self._channels = max(1, int(channels))
        self._block_ms = max(1, int(block_ms))
        self._process_names: Optional[Set[str]] = (
            {str(p).strip().lower() for p in process_names if str(p).strip()} if process_names else None
        )
        self._on_threshold = float(max(0.0, min(1.0, on_threshold)))
        self._off_threshold = float(max(0.0, min(1.0, off_threshold)))
        self._hold_ms = max(0, int(hold_ms))
        self._smooth_samples = max(1, int(smooth_samples))

        self._lock = threading.Lock()
        self._available = False
        self._left = 0.0
        self._right = 0.0
        self._detected = False
        self._reason = "not_initialized"

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start background work for this component so it can begin producing updates."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-meter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Request a clean shutdown for this component and stop ongoing background work."""
        self._stop.set()

    def get_level(self) -> AudioLevel:
        """Return the current level value for callers."""
        with self._lock:
            return AudioLevel(self._available, self._left, self._right, self._detected, self._reason)

    def _set(self, *, available: bool, left: float, right: float, detected: bool, reason: str) -> None:
        """Update  in this component's state."""
        with self._lock:
            self._available = bool(available)
            self._left = float(max(0.0, min(100.0, left)))
            self._right = float(max(0.0, min(100.0, right)))
            self._detected = bool(detected)
            self._reason = str(reason)

    @staticmethod
    def _rms_value(x: np.ndarray) -> float:
        """Rms value."""
        y = x.astype(np.float32)
        return float(np.sqrt(np.mean(y * y))) if y.size else 0.0

    def _pick_loopback_device(self, pa: "pyaudio.PyAudio") -> int:
        """Pick loopback device."""
        if self._device_id:
            try:
                resolved = resolve_device_index(self._device_id)
            except Exception as e:
                print(f"[audio] failed to resolve configured device_id '{self._device_id}': {e}")
                resolved = None
            if resolved is not None:
                return int(resolved)
            print(f"[audio] configured device_id '{self._device_id}' not found; falling back to auto-selection")

        if self._device_index is not None:
            return int(self._device_index)

        try:
            devices = list_audio_devices()
            loopback_candidate = next((d for d in devices if d.is_loopback_like), None)
            if loopback_candidate is not None:
                return int(loopback_candidate.index)
        except Exception:
            pass

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

    def _iter_session_peaks(self) -> Iterable[float]:
        """Iter session peaks."""
        if AudioUtilities is None or IAudioMeterInformation is None:
            return

        for session in AudioUtilities.GetAllSessions():
            ctl = getattr(session, "_ctl", None)
            if ctl is None:
                continue

            # Optionally scope to selected process names.
            if self._process_names is not None:
                proc = getattr(session, "Process", None)
                if proc is None:
                    continue
                try:
                    if proc.name().lower() not in self._process_names:
                        continue
                except Exception:
                    continue

            meter = ctl.QueryInterface(IAudioMeterInformation)
            yield float(meter.GetPeakValue())

    def _run_pycaw(self) -> None:
        """Run the pycaw workflow for this component."""
        if AudioUtilities is None or IAudioMeterInformation is None:
            self._set(available=False, left=0.0, right=0.0, detected=False, reason="pycaw_module_missing")
            return

        has_audio = False
        last_state_change = time.monotonic()
        history = deque(maxlen=self._smooth_samples)
        poll_s = self._block_ms / 1000.0

        self._set(available=True, left=0.0, right=0.0, detected=False, reason="ok")
        while not self._stop.is_set():
            peaks = list(self._iter_session_peaks())
            peak = max(peaks, default=0.0)
            history.append(peak)
            smooth_peak = float(sum(history) / len(history)) if history else peak

            now = time.monotonic()
            elapsed_ms = int((now - last_state_change) * 1000)

            # Schmitt trigger with hold-time to avoid rapid toggles.
            if not has_audio and smooth_peak >= self._on_threshold and elapsed_ms >= self._hold_ms:
                has_audio = True
                last_state_change = now
            elif has_audio and smooth_peak <= self._off_threshold and elapsed_ms >= self._hold_ms:
                has_audio = False
                last_state_change = now

            level_pct = smooth_peak * 100.0
            self._set(available=True, left=level_pct, right=level_pct, detected=has_audio, reason="ok")
            self._stop.wait(timeout=poll_s)

    def _run_loopback(self) -> None:
        """Run the loopback workflow for this component."""
        if pyaudio is None:
            self._set(available=False, left=0.0, right=0.0, detected=False, reason="pyaudiowpatch_module_missing")
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

            self._set(available=True, left=0.0, right=0.0, detected=False, reason="ok")

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

                peak = max(left, right)
                self._set(
                    available=True,
                    left=left * 100.0,
                    right=right * 100.0,
                    detected=peak >= self._on_threshold,
                    reason="ok",
                )
        except Exception as e:
            self._set(available=False, left=0.0, right=0.0, detected=False, reason=f"capture_failed:{e}")
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

    def _run(self) -> None:
        """Execute the main loop for this component until shutdown is requested."""
        if not self._enabled:
            self._set(available=False, left=0.0, right=0.0, detected=False, reason="disabled")
            return

        # Prefer WASAPI audio-session metering when requested because it is independent
        # of endpoint master volume and ideal for binary “audio present” checks.
        if self._backend in ("pycaw", "wasapi", "wasapi_session"):
            self._run_pycaw()
            return

        # Keep existing loopback backend as fallback/default for compatibility.
        self._run_loopback()
