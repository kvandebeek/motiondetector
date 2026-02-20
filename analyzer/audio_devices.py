"""Audio device discovery helpers shared by runtime and CLI tooling."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Optional

try:
    import pyaudiowpatch as pyaudio
except Exception:  # pragma: no cover - optional dependency at runtime
    pyaudio = None


@dataclass(frozen=True)
class AudioDeviceInfo:
    device_id: str
    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_input: bool
    default_output: bool
    is_loopback_like: bool


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return s.strip("-") or "unknown"


def _build_device_id(*, host_api_name: str, device_name: str, ordinal: int) -> str:
    base = f"loopback::{_slug(host_api_name)}::{_slug(device_name)}"
    if ordinal <= 1:
        return base
    return f"{base}::{ordinal}"


def list_audio_devices() -> list[AudioDeviceInfo]:
    """Enumerate input-capable devices useful for audio monitoring."""
    if pyaudio is None:
        raise RuntimeError("pyaudiowpatch_module_missing")

    pa = pyaudio.PyAudio()
    try:
        default_input_index: Optional[int] = None
        default_output_index: Optional[int] = None
        try:
            default_input_index = int(pa.get_default_input_device_info().get("index", -1))
        except Exception:
            default_input_index = None
        try:
            default_output_index = int(pa.get_default_output_device_info().get("index", -1))
        except Exception:
            default_output_index = None

        devices: list[AudioDeviceInfo] = []
        seen_ids: dict[str, int] = {}

        for idx in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(idx)
            max_in = int(info.get("maxInputChannels", 0))
            if max_in <= 0:
                continue

            host_api_index = int(info.get("hostApi", -1))
            host_api_name = "unknown"
            if host_api_index >= 0:
                try:
                    host_api_name = str(pa.get_host_api_info_by_index(host_api_index).get("name", "unknown"))
                except Exception:
                    host_api_name = "unknown"

            name = str(info.get("name", f"device-{idx}"))
            key = f"{_slug(host_api_name)}::{_slug(name)}"
            seen_ids[key] = seen_ids.get(key, 0) + 1
            device_id = _build_device_id(host_api_name=host_api_name, device_name=name, ordinal=seen_ids[key])
            lower_name = name.lower()

            devices.append(
                AudioDeviceInfo(
                    device_id=device_id,
                    index=int(idx),
                    name=name,
                    host_api=host_api_name,
                    max_input_channels=max_in,
                    max_output_channels=int(info.get("maxOutputChannels", 0)),
                    default_input=(default_input_index is not None and int(idx) == int(default_input_index)),
                    default_output=(default_output_index is not None and int(idx) == int(default_output_index)),
                    is_loopback_like=("loopback" in lower_name or "stereo mix" in lower_name),
                )
            )

        return devices
    finally:
        pa.terminate()


def resolve_device_index(device_id: str) -> Optional[int]:
    """Resolve stored device identifier to current runtime index."""
    for dev in list_audio_devices():
        if dev.device_id == device_id:
            return int(dev.index)
    return None
