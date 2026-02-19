"""Loopback audio metering for dashboard/status payloads."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class AudioMeterConfig:
    enabled: bool = True
    backend: str = "pyaudiowpatch"  # pyaudiowpatch | soundcard
    device_substr: str = ""
    samplerate: int = 48_000
    channels: int = 2
    block_ms: int = 250
    calib_sec: float = 2.0
    factor: float = 2.5
    abs_min: float = 0.00012


class AudioLoopbackMeter:
    def __init__(self, cfg: AudioMeterConfig) -> None:
        self._cfg = cfg
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = {
            "state": "DISABLED" if not cfg.enabled else "ERROR",
            "reason": "disabled" if not cfg.enabled else "initializing",
            "level": 0.0,
            "rms": 0.0,
            "peak": 0.0,
            "baseline": 0.0,
            "threshold": 0.0,
            "detected": False,
            "timestamp": time.time(),
        }

    def start(self) -> None:
        if not bool(self._cfg.enabled):
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-loopback-meter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def get_payload(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def _set_latest(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._latest = dict(payload)

    @staticmethod
    def _rms(x: np.ndarray) -> float:
        y = x.astype(np.float32, copy=False)
        return float(np.sqrt(np.mean(y * y))) if y.size else 0.0

    @staticmethod
    def _to_mono(data: np.ndarray) -> np.ndarray:
        if data.ndim == 1:
            return data
        if data.ndim == 2 and data.shape[1] > 1:
            return data.mean(axis=1)
        return data.reshape(-1)

    def _run(self) -> None:
        backend = str(self._cfg.backend or "pyaudiowpatch").strip().lower()
        if backend in ("auto", "default"):
            backend = "pyaudiowpatch"

        try:
            if backend == "pyaudiowpatch":
                self._run_pyaudio_loopback()
                return
            if backend == "soundcard":
                self._run_soundcard_loopback()
                return
            raise RuntimeError(f"unsupported_audio_backend: {backend}")
        except Exception as e:
            self._set_latest(
                {
                    "state": "ERROR",
                    "reason": f"audio_capture_failed: {e}",
                    "level": 0.0,
                    "rms": 0.0,
                    "peak": 0.0,
                    "baseline": 0.0,
                    "threshold": 0.0,
                    "detected": False,
                    "timestamp": time.time(),
                }
            )

    def _run_pyaudio_loopback(self) -> None:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            dev = self._find_pyaudio_device(pa, str(self._cfg.device_substr or ""))
            max_in = int(dev.get("maxInputChannels", 0))
            if max_in <= 0:
                raise RuntimeError("selected loopback device has no input channels")

            channels = max(1, min(int(self._cfg.channels), max_in))
            samplerate = max(8_000, int(self._cfg.samplerate))
            block_ms = max(20, int(self._cfg.block_ms))
            frames = max(256, int(round((block_ms / 1000.0) * samplerate)))

            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=samplerate,
                input=True,
                input_device_index=int(dev["index"]),
                frames_per_buffer=frames,
            )
            try:
                baseline, threshold = self._calibrate_stream(
                    read_fn=lambda: self._read_pyaudio_block(stream=stream, frames=frames, channels=channels),
                    block_ms=block_ms,
                )
                self._run_sampling_loop(
                    read_fn=lambda: self._read_pyaudio_block(stream=stream, frames=frames, channels=channels),
                    baseline=baseline,
                    threshold=threshold,
                )
            finally:
                stream.stop_stream()
                stream.close()
        finally:
            pa.terminate()

    @staticmethod
    def _find_pyaudio_device(pa: Any, needle: str) -> dict[str, Any]:
        needle_l = needle.strip().lower()
        devs = [d for d in pa.get_loopback_device_info_generator()]
        if not devs:
            raise RuntimeError("no loopback devices found")
        if not needle_l:
            return devs[0]
        for d in devs:
            if needle_l in str(d.get("name", "")).lower():
                return d
        available = ", ".join(str(d.get("name", "?")) for d in devs)
        raise RuntimeError(f'no loopback device matches "{needle}"; available={available}')

    def _read_pyaudio_block(self, *, stream: Any, frames: int, channels: int) -> np.ndarray:
        raw = stream.read(frames, exception_on_overflow=False)
        arr = np.frombuffer(raw, dtype=np.float32)
        if channels > 1 and arr.size >= channels:
            arr = arr.reshape((-1, channels)).mean(axis=1)
        return arr

    def _run_soundcard_loopback(self) -> None:
        import soundcard as sc

        mic = self._select_soundcard_loopback(sc)
        samplerate = max(8_000, int(self._cfg.samplerate))
        block_ms = max(20, int(self._cfg.block_ms))
        frames = max(256, int(round((block_ms / 1000.0) * samplerate)))
        channels = max(1, int(self._cfg.channels))

        with mic.recorder(samplerate=samplerate, channels=channels, blocksize=frames) as rec:
            baseline, threshold = self._calibrate_stream(
                read_fn=lambda: self._to_mono(np.asarray(rec.record(numframes=frames), dtype=np.float32)),
                block_ms=block_ms,
            )
            self._run_sampling_loop(
                read_fn=lambda: self._to_mono(np.asarray(rec.record(numframes=frames), dtype=np.float32)),
                baseline=baseline,
                threshold=threshold,
            )

    def _select_soundcard_loopback(self, sc: Any) -> Any:
        needle = str(self._cfg.device_substr or "").strip().lower()
        mics = list(sc.all_microphones(include_loopback=True))
        if not mics:
            raise RuntimeError("no loopback microphones found")
        if needle:
            for m in mics:
                if needle in str(getattr(m, "name", "")).lower():
                    return m
        return mics[0]

    def _calibrate_stream(self, *, read_fn: Any, block_ms: int) -> tuple[float, float]:
        cal_blocks = max(1, int(round(float(self._cfg.calib_sec) / (block_ms / 1000.0))))
        vals: list[float] = []
        for _ in range(cal_blocks):
            if self._stop.is_set():
                break
            vals.append(self._rms(read_fn()))
        baseline = float(np.median(np.asarray(vals, dtype=np.float32))) if vals else 0.0
        threshold = max(float(self._cfg.abs_min), baseline * float(self._cfg.factor))
        return baseline, threshold

    def _run_sampling_loop(self, *, read_fn: Any, baseline: float, threshold: float) -> None:
        while not self._stop.is_set():
            mono = read_fn()
            rms = self._rms(mono)
            peak = float(np.max(np.abs(mono))) if mono.size else 0.0
            detected = bool(rms >= threshold)
            level = float(min(1.0, rms / max(threshold, 1e-12)))
            self._set_latest(
                {
                    "state": "OK",
                    "reason": "ok",
                    "level": level,
                    "rms": float(rms),
                    "peak": float(peak),
                    "baseline": float(baseline),
                    "threshold": float(threshold),
                    "detected": detected,
                    "timestamp": time.time(),
                }
            )
