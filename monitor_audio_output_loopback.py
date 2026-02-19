# File commentary: monitor_audio_output_loopback.py - This file holds logic used by the motion detector project.
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

import numpy as np
import pyaudiowpatch as pyaudio


def rms_value(x: np.ndarray) -> float:
    """Handle rms value for this module."""
    y = x.astype(np.float32)
    return float(np.sqrt(np.mean(y * y)))


def main(argv: Optional[list[str]] = None) -> int:
    """Run the main application flow and return an exit code for the process."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-index", type=int, required=True, help="Loopback device index (e.g. 13 or 14).")
    parser.add_argument("--samplerate", type=int, default=48_000)
    parser.add_argument("--channels", type=int, default=2)
    parser.add_argument("--block-ms", type=int, default=250)
    parser.add_argument("--threshold", type=float, default=0.0025)
    args = parser.parse_args(argv)

    pa = pyaudio.PyAudio()
    try:
        dev = pa.get_device_info_by_index(int(args.device_index))
        dev_name = str(dev.get("name", ""))
        max_in = int(dev.get("maxInputChannels", 0))
        if max_in <= 0:
            raise RuntimeError(f"Device {args.device_index} is not an input/loopback device: {dev_name}")

        channels = int(args.channels)
        if channels < 1:
            channels = 1
        if channels > max_in:
            channels = max_in

        frames = int(round((args.block_ms / 1000.0) * args.samplerate))
        if frames < 256:
            frames = 256

        print(f"Loopback device: [{args.device_index}] {dev_name}")
        print(f"sr={args.samplerate} ch={channels} block={args.block_ms}ms threshold={args.threshold}")
        print("Press Ctrl+C to stop.\n")

        stream = pa.open(
            format=pyaudio.paFloat32,
            channels=channels,
            rate=int(args.samplerate),
            input=True,
            input_device_index=int(args.device_index),
            frames_per_buffer=frames,
        )

        try:
            while True:
                raw = stream.read(frames, exception_on_overflow=False)
                arr = np.frombuffer(raw, dtype=np.float32)
                if channels > 1:
                    arr = arr.reshape((-1, channels)).mean(axis=1)

                rms = rms_value(arr) if arr.size else 0.0
                peak = float(np.max(np.abs(arr))) if arr.size else 0.0
                detected = rms >= float(args.threshold)

                print(f"RMS={rms:.6f}  Peak={peak:.6f}  Detected={'YES' if detected else 'NO'}")
                time.sleep(0.0)
        finally:
            stream.stop_stream()
            stream.close()

    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        pa.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
