#!/usr/bin/env python3
"""CLI utility to discover and select an audio monitoring device."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analyzer.audio_devices import AudioDeviceInfo, list_audio_devices


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="audio-device-selector")
    p.add_argument("--config", default="./config/config.json", help="Path to config.json")
    p.add_argument("--select", type=int, default=None, help="Select device by numeric index (non-interactive)")
    return p.parse_args()


def _print_devices(devices: list[AudioDeviceInfo]) -> None:
    print("Available audio monitoring devices:")
    for idx, dev in enumerate(devices):
        flags: list[str] = []
        if dev.default_input:
            flags.append("default-input")
        if dev.default_output:
            flags.append("default-output")
        if dev.is_loopback_like:
            flags.append("loopback-like")
        flags_text = f" [{', '.join(flags)}]" if flags else ""
        print(
            f"[{idx}] id={dev.device_id} | name={dev.name} | host_api={dev.host_api} "
            f"| in={dev.max_input_channels} | out={dev.max_output_channels}{flags_text}"
        )


def _prompt_for_index(count: int) -> int:
    while True:
        raw = input("Select device number: ").strip()
        try:
            value = int(raw)
        except ValueError:
            print("Invalid number. Please enter one of the listed indexes.")
            continue

        if 0 <= value < count:
            return value
        print(f"Out of range. Choose a value between 0 and {count - 1}.")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return data


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def main() -> int:
    args = _parse_args()

    try:
        devices = list_audio_devices()
    except Exception as e:
        print(f"ERROR: unable to enumerate audio devices: {e}", file=sys.stderr)
        return 2

    if not devices:
        print("ERROR: no suitable input/loopback audio devices found.", file=sys.stderr)
        return 3

    _print_devices(devices)

    if args.select is None:
        selected_index = _prompt_for_index(len(devices))
    else:
        selected_index = int(args.select)
        if selected_index < 0 or selected_index >= len(devices):
            print(f"ERROR: --select index must be between 0 and {len(devices) - 1}", file=sys.stderr)
            return 4

    selected = devices[selected_index]

    config_path = Path(args.config)
    try:
        raw = _load_json(config_path)
        audio_obj = raw.get("audio")
        if not isinstance(audio_obj, dict):
            audio_obj = {}
            raw["audio"] = audio_obj

        audio_obj["device_id"] = selected.device_id
        audio_obj["device_index"] = int(selected.index)
        _atomic_write_json(config_path, raw)
    except Exception as e:
        print(f"ERROR: failed to update {config_path}: {e}", file=sys.stderr)
        return 5

    print(f"Saved audio.device_id='{selected.device_id}' and audio.device_index={selected.index} to {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
