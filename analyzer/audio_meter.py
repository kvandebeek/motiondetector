"""Loopback audio metering for dashboard/status payloads.

The meter runs in a background thread and samples the system output loopback stream,
producing normalized levels plus detection metadata suitable for JSON payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class AudioMeterConfig:
    enabled: bool = True
    device_substr: str = ""
    samplerate: int = 48_000
    channels: int = 2
    block_ms: int = 250
    calib_sec: float = 2.0
    factor: float = 2.5
    abs_min: float = 0.00012


class AudioLoopbackMeter:
    """Background loopback audio monitor.

    Payload shape emitted by `get_payload()`:
    {
      "state": "OK"|"ERROR"|"DISABLED",
      "reason": str,
      "level": float[0..1],
      "rms": float,
      "peak": float,
      "baseline": float,
      "threshold": float,
      "detected": bool,
      "timestamp": float
    }
    """

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
        if y.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(y * y)))

    @staticmethod
    def _to_mono(data: np.ndarray) -> np.ndarray:
        if data.ndim == 1:
            return data
        if data.ndim == 2 and data.shape[1] > 1:
            return data.mean(axis=1)
        return data.reshape(-1)

    def _select_loopback_mic(self, sc: Any) -> Any:
        needle = str(self._cfg.device_substr or "").strip().lower()
        mics = list(sc.all_microphones(include_loopback=True))
        if not mics:
            raise RuntimeError("No loopback microphones found")

        if needle:
            matches = [m for m in mics if needle in str(getattr(m, "name", "")).lower()]
            if matches:
                return matches[0]

        # Prefer default speaker loopback when possible.
        try:
            spk = sc.default_speaker()
            spk_name = str(getattr(spk, "name", "")).lower()
            if spk_name:
                for m in mics:
                    if spk_name in str(getattr(m, "name", "")).lower():
                        return m
        except Exception:
            pass

        # Fallback to first loopback microphone.
        return mics[0]

    def _run(self) -> None:
        try:
            import soundcard as sc
        except Exception as e:
            self._set_latest(
                {
                    "state": "ERROR",
                    "reason": f"audio_import_failed: {e}",
                    "level": 0.0,
                    "rms": 0.0,
                    "peak": 0.0,
                    "baseline": 0.0,
                    "threshold": 0.0,
                    "detected": False,
                    "timestamp": time.time(),
                }
            )
            return

        samplerate = max(8_000, int(self._cfg.samplerate))
        block_ms = max(20, int(self._cfg.block_ms))
        frames = max(256, int(round((block_ms / 1000.0) * samplerate)))

        try:
            mic = self._select_loopback_mic(sc)
            channels = max(1, int(self._cfg.channels))

            with mic.recorder(samplerate=samplerate, channels=channels, blocksize=frames) as rec:
                cal_blocks = max(1, int(round(float(self._cfg.calib_sec) / (block_ms / 1000.0))))
                cal_vals: list[float] = []
                for _ in range(cal_blocks):
                    if self._stop.is_set():
                        return
                    block = np.asarray(rec.record(numframes=frames), dtype=np.float32)
                    mono = self._to_mono(block)
                    cal_vals.append(self._rms(mono))

                baseline = float(np.median(np.asarray(cal_vals, dtype=np.float32))) if cal_vals else 0.0
                threshold = max(float(self._cfg.abs_min), baseline * float(self._cfg.factor))

                while not self._stop.is_set():
                    block = np.asarray(rec.record(numframes=frames), dtype=np.float32)
                    mono = self._to_mono(block)
                    rms = self._rms(mono)
                    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
                    detected = bool(rms >= threshold)
                    denom = max(threshold, 1e-12)
                    level = float(min(1.0, rms / denom))

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
