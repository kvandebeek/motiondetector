# testdata/engine.py
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Optional, Tuple

import numpy as np

from testdata.profile import TestDataProfile
from testdata.settings import TestDataSettings


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass(frozen=True)
class SubtitleOverlay:
    text: str
    fg_rgb: tuple[int, int, int]
    alpha: float
    x_px: int
    y_px: int


@dataclass(frozen=True)
class FrameOut:
    rgb: np.ndarray
    scene_index: int
    scene_name: str
    phase_name: str
    scene_time_s: float
    ema_activity: float
    expected_state: str
    subtitle: Optional[SubtitleOverlay] = None


class TestDataEngine:
    """
    Synthetic "streaming-like" scene generator used to drive the detector and collect tuning logs.

    Scenes 1–18: original set (kept)
    Scenes 19–30: streaming realism set, each with variants v1/v2/v3 (encoded in scene_name/phase)

    Conventions:
    - scene_index is 1-based and stable across runs
    - phase_name is used for sub-phases inside a scene (e.g. scene 4, 12, 13, 26)
    - expected_state is the intent label for test evaluation
    """

    def __init__(self, *, settings: TestDataSettings, seed: int = 1337, profile_name: str = "default") -> None:
        self._s = settings
        self._rng = random.Random(int(seed))
        self._profile = TestDataProfile.from_name(profile_name)

        self._w = 320
        self._h = 240

        self._t = 0.0
        self._scene0 = 0
        self._scene_t = 0.0

        # generator-side metric (EMA of generator-defined activity proxy)
        self._ema = 0.0
        self._prev_gray: Optional[np.ndarray] = None

        # shared “moving pixels” state
        self._px_positions: list[Tuple[int, int]] = []
        self._px_count = 8

        # blocky subtitle state
        self._sub_text = ""
        self._sub_x = 0.0
        self._sub_y = 0.0
        self._sub_speed_px_s = 12.0

        # real subtitle state (overlay-only)
        self._real_sub_text = "This is a synthetic subtitle test."

        # pan state
        self._pan_base: Optional[np.ndarray] = None
        self._pan_offset_x = 0

        # noise seeds
        self._noise_phase_seed = 0

        # black blink state
        self._blink_on = False
        self._next_blink_s = 7.0

        # subtitle crawl state
        self._crawl_text = "Buffering… please wait."
        self._crawl_x = 0.0
        self._crawl_y = 0.0

        # reusable static “content” texture
        self._static_texture: Optional[np.ndarray] = None
        self._static_texture_seed = 0

        # grain state
        self._grain_seed = 0
        self._grain_next_update_s = 0.0
        self._grain_cache: Optional[np.ndarray] = None

        # decoder/macroblock style noise
        self._mb_seed = 0
        self._mb_cache: Optional[np.ndarray] = None
        self._mb_next_update_s = 0.0

        # logo bug state (static logo that may flicker)
        self._logo_on = True
        self._logo_next_toggle_s = 0.0

        # captions crawl band
        self._cap_text = "CAPTIONS: example subtitle line that slowly fades…"
        self._cap_alpha = 0.0
        self._cap_dir = 1.0

        # ticker band
        self._ticker_x = 0.0
        self._ticker_text = "BREAKING: sample news ticker…  "

        # hard-cuts state
        self._cut_a_seed = 11_111
        self._cut_b_seed = 22_222
        self._cut_a: Optional[np.ndarray] = None
        self._cut_b: Optional[np.ndarray] = None

        # freeze state
        self._freeze_base: Optional[np.ndarray] = None
        self._freeze_phase_seed = 33_333
        self._freeze_last_refresh_s = 0.0

        # detailed pan state
        self._dpan_base: Optional[np.ndarray] = None
        self._dpan_u = 0.0

        # film grain / brightness pump state
        self._pump_phase = 0.0

        # spinner state (streaming realism)
        self._spinner_phase = 0.0

        # NEW: true NO_MOTION micro-noise state for scene 1
        self._no_motion_frame_i = 0
        self._no_motion_noise: Optional[np.ndarray] = None

        # durations
        base_1_to_9 = [60.0, 60.0, 20.0, 30.0, 60.0, 30.0, 30.0, 60.0, 60.0]
        base_10_to_18 = [
            30.0,  # 10 real subtitles fade W/B
            30.0,  # 11 real subtitles fade B/W
            45.0,  # 12 pixel-size calibration (3 phases)
            0.0,   # 13 tile sweep (computed from grid)
            30.0,  # 14 slow pan
            20.0,  # 15 noise below NO_MOTION
            20.0,  # 16 noise between NO and LOW
            40.0,  # 17 black + UI blink
            45.0,  # 18 subtitle crawl across tiles
        ]
        base_19_to_30 = [
            30.0,  # 19 long black with subtle noise
            30.0,  # 20 static show with small logo bug
            30.0,  # 21 static show with captions fade
            30.0,  # 22 static show with ticker crawl
            30.0,  # 23 film grain in dark scene
            30.0,  # 24 brightness pump (ABL-ish)
            30.0,  # 25 loading spinner overlay
            45.0,  # 26 hard cuts + decoder settle spike (phase cut/stable)
            45.0,  # 27 freeze frame with periodic macro refresh
            45.0,  # 28 camera pan on detailed content
            30.0,  # 29 banded gradients / compression shimmer
            45.0,  # 30 scrolling credits / slow vertical text
        ]

        self._durations = self._build_durations(base_1_to_9, base_10_to_18, base_19_to_30)
        self._init_scene(0)

    # ---------------- public ----------------

    def set_size(self, *, w: int, h: int) -> None:
        self._w = max(1, int(w))
        self._h = max(1, int(h))
        self._prev_gray = None
        self._pan_base = None
        self._static_texture = None
        self._mb_cache = None
        self._grain_cache = None
        self._cut_a = None
        self._cut_b = None
        self._freeze_base = None
        self._dpan_base = None
        self._no_motion_noise = None
        self._no_motion_frame_i = 0

    def next_frame(self) -> FrameOut:
        dt = 1.0 / max(1.0, float(self._s.fps))
        self._t += dt
        self._scene_t += dt

        if self._scene_t >= self._durations[self._scene0]:
            self._scene0 = (self._scene0 + 1) % len(self._durations)
            self._init_scene(self._scene0)

        rgb, subtitle = self._render_scene(scene0=self._scene0, dt=dt)

        # generator-side activity proxy (mean abs diff, scaled, normalized, EMA)
        gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype(np.uint8)
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
        else:
            diff = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16)).astype(np.uint8)
            self._prev_gray = gray

            mean_diff01 = float(diff.mean() / 255.0)
            mean_raw = min(1.0, mean_diff01 * float(self._s.diff_gain))
            mean_full = max(1e-9, float(self._s.mean_full_scale))
            activity = _clamp01(mean_raw / mean_full)
            a = float(self._s.ema_alpha)
            self._ema = (a * activity) + ((1.0 - a) * self._ema)

        idx1 = int(self._scene0) + 1
        return FrameOut(
            rgb=rgb,
            scene_index=idx1,
            scene_name=self._scene_name(idx1),
            phase_name=self._phase_name(idx1),
            scene_time_s=float(self._scene_t),
            ema_activity=float(self._ema),
            expected_state=self._expected_state(idx1),
            subtitle=subtitle,
        )

    # ---------------- durations / metadata ----------------

    def _build_durations(self, base_1_to_9: list[float], base_10_to_18: list[float], base_19_to_30: list[float]) -> list[float]:
        d: list[float] = []

        s1 = float(getattr(self._profile, "scale_existing_1_to_9", 1.0))
        for x in base_1_to_9:
            d.append(max(5.0, round(float(x) * s1)))

        s2 = float(getattr(self._profile, "scale_new_10_plus", 1.0))
        for i, x in enumerate(base_10_to_18):
            if i == 3:  # scene 13 tile sweep computed from grid
                tiles = max(1, int(self._s.grid_rows) * int(self._s.grid_cols))
                per_tile = float(getattr(self._profile, "tile_sweep_seconds_per_tile", 1.0))
                d.append(max(8.0, round(float(tiles) * per_tile)))
            else:
                d.append(max(5.0, round(float(x) * s2)))

        for x in base_19_to_30:
            d.append(max(5.0, round(float(x) * s2)))

        return d

    def _variant_tag(self, idx1: int) -> str:
        v = (int(idx1) + int(self._static_texture_seed)) % 3
        return "v1" if v == 0 else "v2" if v == 1 else "v3"

    def _scene_name(self, idx1: int) -> str:
        if idx1 >= 19:
            return f"{self._scene_name_base(idx1)} ({self._variant_tag(idx1)})"
        return self._scene_name_base(idx1)

    def _scene_name_base(self, idx1: int) -> str:
        return {
            1: "Random pixels below NO_MOTION threshold",
            2: "Random pixels below LOW_ACTIVITY threshold",
            3: "Random pixels above LOW_ACTIVITY threshold",
            4: "Alternating threshold boundary test (1/2/3)",
            5: "Slow fade black ↔ 50% grey (3 cycles)",
            6: "Subtitles (blocky) white on black (slow)",
            7: "Subtitles (blocky) black on white (slow)",
            8: "Static scene with alternating regions (NO/LOW/MOTION)",
            9: "Static scene with spinner (connection loss mimic)",
            10: "Real subtitles fade (white on black)",
            11: "Real subtitles fade (black on white)",
            12: "Pixel-size calibration (2×2 / 3×3 / 4×4)",
            13: "One-tile-only motion sweep (tile-by-tile)",
            14: "Slow pan (1 px drift)",
            15: "Compression noise (macroblocks) below NO_MOTION",
            16: "Compression noise (macroblocks) between NO and LOW",
            17: "Black screen with occasional UI blink",
            18: "Subtitle crawl across tiles (real text)",
            19: "Long black screen with subtle noise",
            20: "Static show with small logo bug (corner)",
            21: "Captions band fade (bottom)",
            22: "News ticker crawl (bottom)",
            23: "Film grain in dark scene",
            24: "Brightness pump / ABL-like drift",
            25: "Loading spinner overlay (buffering)",
            26: "Hard cuts + decoder settle spike",
            27: "Freeze frame with periodic refresh blocks",
            28: "Camera pan on detailed content",
            29: "Compression shimmer on gradients",
            30: "Scrolling credits / vertical text",
        }.get(idx1, f"Scene {idx1}")

    def _phase_name(self, idx1: int) -> str:
        if idx1 == 4:
            p = int(self._scene_t // (self._durations[self._scene0] / 3.0)) % 3
            return "phase=1" if p == 0 else "phase=2" if p == 1 else "phase=3"
        if idx1 == 12:
            per = self._durations[self._scene0] / 3.0
            p = int(self._scene_t // per) % 3
            return "dot=2x2" if p == 0 else "dot=3x3" if p == 1 else "dot=4x4"
        if idx1 == 13:
            tiles = max(1, int(self._s.grid_rows) * int(self._s.grid_cols))
            per = self._durations[self._scene0] / float(tiles)
            ti = int(self._scene_t // per)
            return f"tile={ti}"
        if idx1 == 26:
            per = float(self._cut_period_s())
            cut_i = int(self._scene_t // per)
            within = float(self._scene_t - (cut_i * per))
            return f"cut={cut_i} phase={'cut' if within < self._cut_spike_s() else 'stable'}"
        if idx1 >= 19:
            return self._variant_tag(idx1)
        return ""

    def _expected_state(self, idx1: int) -> str:
        if idx1 == 1:
            return "NO_MOTION_NOSOUNDHARDWARE"
        if idx1 == 2:
            return "LOW_ACTIVITY_NOSOUNDHARDWARE"
        if idx1 == 3:
            return "MOTION_NOSOUNDHARDWARE"
        if idx1 == 4:
            p = int(self._scene_t // (self._durations[self._scene0] / 3.0)) % 3
            return "NO_MOTION_NOSOUNDHARDWARE" if p == 0 else "LOW_ACTIVITY_NOSOUNDHARDWARE" if p == 1 else "MOTION_NOSOUNDHARDWARE"
        if idx1 in (5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18):
            return "LOW_ACTIVITY_NOSOUNDHARDWARE"
        if idx1 == 8:
            return "MOTION_NOSOUNDHARDWARE"

        if idx1 == 19:
            return "NO_MOTION_NOSOUNDHARDWARE"
        if idx1 in (20, 21, 22, 23, 24, 25, 27, 29, 30):
            return "LOW_ACTIVITY_NOSOUNDHARDWARE"
        if idx1 == 26:
            per = float(self._cut_period_s())
            cut_i = int(self._scene_t // per)
            within = float(self._scene_t - (cut_i * per))
            return "MOTION_NOSOUNDHARDWARE" if within < self._cut_spike_s() else "NO_MOTION_NOSOUNDHARDWARE"
        if idx1 == 28:
            return "MOTION_NOSOUNDHARDWARE"

        return "LOW_ACTIVITY_NOSOUNDHARDWARE"

    # ---------------- scene init ----------------

    def _init_scene(self, idx0: int) -> None:
        self._scene_t = 0.0
        self._prev_gray = None
        self._ema = 0.0  # scene-local calibration

        idx1 = idx0 + 1

        if idx1 in (1, 2, 3, 4, 12, 13):
            self._px_positions = [
                (self._rng.randrange(0, self._w), self._rng.randrange(0, self._h)) for _ in range(max(1, self._px_count))
            ]

        if idx1 == 1:
            self._no_motion_frame_i = 0
            self._no_motion_noise = None

        if idx1 in (6, 7):
            self._sub_text = "Lorem ipsum…"
            self._sub_x = 0.0
            self._sub_y = float(self._h) * 0.78
            self._sub_speed_px_s = 12.0

        if idx1 == 18:
            self._crawl_x = float(self._w)
            self._crawl_y = float(self._h) * 0.80

        if idx1 in (14, 28):
            self._pan_base = None
            self._pan_offset_x = 0
            self._dpan_base = None
            self._dpan_u = 0.0

        if idx1 in (15, 16, 19, 23, 29):
            self._noise_phase_seed = self._rng.randrange(1, 2**31 - 1)
            self._mb_seed = self._rng.randrange(1, 2**31 - 1)
            self._mb_cache = None
            self._mb_next_update_s = 0.0
            self._grain_seed = self._rng.randrange(1, 2**31 - 1)
            self._grain_cache = None
            self._grain_next_update_s = 0.0

        if idx1 == 17:
            self._blink_on = False
            self._next_blink_s = 2.0 + self._rng.random() * 3.0

        if idx1 == 20:
            self._logo_on = True
            self._logo_next_toggle_s = 1.0 + self._rng.random() * 2.0

        if idx1 == 21:
            self._cap_alpha = 0.0
            self._cap_dir = 1.0

        if idx1 == 22:
            self._ticker_x = float(self._w)

        if idx1 == 24:
            self._pump_phase = 0.0

        if idx1 == 25:
            self._spinner_phase = 0.0

        if idx1 == 26:
            self._cut_a = None
            self._cut_b = None

        if idx1 == 27:
            self._freeze_base = None
            self._freeze_last_refresh_s = 0.0

        # base texture seed changes per scene to keep variety but determinism
        self._static_texture_seed = (idx1 * 9973) ^ 0xA5A5A5
        self._static_texture = None

    # ---------------- render dispatch ----------------

    def _render_scene(self, *, scene0: int, dt: float) -> tuple[np.ndarray, Optional[SubtitleOverlay]]:
        idx1 = scene0 + 1

        if idx1 == 1:
            return self._scene_pixels(target="below_no_motion"), None
        if idx1 == 2:
            return self._scene_pixels(target="below_low_activity"), None
        if idx1 == 3:
            return self._scene_pixels(target="above_low_activity"), None
        if idx1 == 4:
            per = self._durations[scene0] / 3.0
            p = int(self._scene_t // per) % 3
            tgt = "below_no_motion" if p == 0 else "below_low_activity" if p == 1 else "above_low_activity"
            return self._scene_pixels(target=tgt), None
        if idx1 == 5:
            return self._scene_fade(), None
        if idx1 == 6:
            return self._scene_subtitles_blocky(fg="white", bg="black", dt=dt), None
        if idx1 == 7:
            return self._scene_subtitles_blocky(fg="black", bg="white", dt=dt), None
        if idx1 == 8:
            return self._scene_static_regions(), None
        if idx1 == 9:
            return self._scene_spinner(), None
        if idx1 == 10:
            return self._scene_real_subtitles_fade(fg="white", bg="black"), self._subtitle_overlay(fg="white", bg="black", fade=True)
        if idx1 == 11:
            return self._scene_real_subtitles_fade(fg="black", bg="white"), self._subtitle_overlay(fg="black", bg="white", fade=True)
        if idx1 == 12:
            return self._scene_pixel_size_calibration(), None
        if idx1 == 13:
            return self._scene_one_tile_sweep(), None
        if idx1 == 14:
            return self._scene_slow_pan(), None
        if idx1 == 15:
            return self._scene_compression_noise(mode="below_no"), None
        if idx1 == 16:
            return self._scene_compression_noise(mode="between"), None
        if idx1 == 17:
            return self._scene_black_with_blink(dt=dt), None
        if idx1 == 18:
            return self._scene_subtitle_crawl(dt=dt), self._subtitle_crawl_overlay()

        if idx1 == 19:
            return self._scene_long_black_with_noise(dt=dt), None
        if idx1 == 20:
            return self._scene_logo_bug(dt=dt), None
        if idx1 == 21:
            return self._scene_captions_fade(dt=dt), self._captions_overlay()
        if idx1 == 22:
            return self._scene_ticker_crawl(dt=dt), self._ticker_overlay()
        if idx1 == 23:
            return self._scene_film_grain(dt=dt), None
        if idx1 == 24:
            return self._scene_brightness_pump(dt=dt), None
        if idx1 == 25:
            return self._scene_loading_spinner(dt=dt), None
        if idx1 == 26:
            return self._scene_hard_cuts(dt=dt), None
        if idx1 == 27:
            return self._scene_freeze_with_refresh(dt=dt), None
        if idx1 == 28:
            return self._scene_detailed_pan(dt=dt), None
        if idx1 == 29:
            return self._scene_gradient_shimmer(dt=dt), None
        if idx1 == 30:
            return self._scene_scrolling_credits(dt=dt), None

        return np.zeros((self._h, self._w, 3), dtype=np.uint8), None

    # ---------------- scenes 1–18 (kept, scene 1 improved) ----------------

    def _scene_pixels(self, *, target: str) -> np.ndarray:
        if target == "below_no_motion":
            # (keep your existing below_no_motion implementation unchanged)
            self._no_motion_frame_i += 1
            update_every = max(1, int(round(float(self._s.fps) * 0.5)))
            amp = 1
            density = 0.0002

            if self._no_motion_noise is None or (self._no_motion_frame_i % update_every) == 0:
                if self._noise_phase_seed == 0:
                    self._noise_phase_seed = self._rng.randrange(1, 2**31 - 1)

                rng = np.random.default_rng(int(self._noise_phase_seed) + int(self._no_motion_frame_i) + 10_000)
                mask = rng.random((self._h, self._w)) < float(density)

                jitter = np.zeros((self._h, self._w), dtype=np.int16)
                n = int(mask.sum())
                if n > 0:
                    jitter_vals = rng.integers(-amp, amp + 1, size=n, dtype=np.int16)
                    jitter[mask] = jitter_vals
                self._no_motion_noise = jitter

            base = np.zeros((self._h, self._w, 3), dtype=np.uint8)
            if self._no_motion_noise is None:
                return base
            return np.clip(base.astype(np.int16) + self._no_motion_noise[:, :, None], 0, 255).astype(np.uint8)

        # ------------------------------------------------------------
        # NEW: controlled sparse noise for LOW/MOTION targets
        # ------------------------------------------------------------
        if not hasattr(self, "_px_field_i"):
            self._px_field_i = 0
            self._px_field: Optional[np.ndarray] = None

        self._px_field_i += 1

        low_t = float(self._s.low_activity_threshold)

        # Choose a target EMA band for the generator.
        # IMPORTANT: scene-2 must land clearly BELOW low_t; scene-3 clearly ABOVE.
        if target == "below_low_activity":
            target_ema = 0.85 * low_t     # was 0.85*low_t (too high in practice)
            amp = 6                       # small per-pixel delta
            update_every = 1
        else:
            target_ema = 1.80 * low_t     # motion band
            amp = 18
            update_every = 1              # change every frame for clear motion

        mean_full = max(1e-9, float(self._s.mean_full_scale))
        diff_gain = max(1e-9, float(self._s.diff_gain))

        # Desired mean abs diff in [0..1] of grayscale, before /mean_full normalization
        desired_mean_diff01 = _clamp01((target_ema * mean_full) / diff_gain)

        # For a sparse field where changed pixels move by ~amp, expected mean abs diff:
        #   mean_diff01 ≈ density * (amp / 255)
        # so:
        density = _clamp01(desired_mean_diff01 * 255.0 / max(1.0, float(amp)))

        # Keep it sane: we don't want huge density for these synthetic scenes
        density = min(density, 0.02)  # max 2% of pixels per update

        if self._noise_phase_seed == 0:
            self._noise_phase_seed = self._rng.randrange(1, 2**31 - 1)

        if self._px_field is None or (self._px_field_i % update_every) == 0:
            rng = np.random.default_rng(int(self._noise_phase_seed) + int(self._px_field_i) + (20_000 if target == "below_low_activity" else 30_000))

            mask = rng.random((self._h, self._w)) < float(density)

            field = np.zeros((self._h, self._w), dtype=np.int16)
            n = int(mask.sum())
            if n > 0:
                # Signed jitter; avoid 0 too often so changes actually register
                vals = rng.integers(-amp, amp + 1, size=n, dtype=np.int16)
                vals[vals == 0] = 1
                field[mask] = vals

            self._px_field = field

        base = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        if self._px_field is None:
            return base

        return np.clip(base.astype(np.int16) + self._px_field[:, :, None], 0, 255).astype(np.uint8)


    def _scene_fade(self) -> np.ndarray:
        cycle = 20.0
        t = self._scene_t % cycle
        v = int(round(128.0 * (t / 10.0))) if t <= 10.0 else int(round(128.0 * (1.0 - ((t - 10.0) / 10.0))))
        return np.full((self._h, self._w, 3), v, dtype=np.uint8)

    def _scene_subtitles_blocky(self, *, fg: str, bg: str, dt: float) -> np.ndarray:
        bg_v = 0 if bg == "black" else 255
        fg_v = 255 if fg == "white" else 0
        frame = np.full((self._h, self._w, 3), bg_v, dtype=np.uint8)

        self._sub_x += float(self._sub_speed_px_s) * dt
        if self._sub_x > float(self._w * 0.25):
            if self._rng.random() < 0.07:
                self._sub_text = "…" if self._sub_text != "…" else "Lorem ipsum…"
                self._sub_x = 0.0

        x0 = int(self._w * 0.20 + (self._sub_x % max(1.0, float(self._w) * 0.10)))
        y0 = int(self._h * 0.78)
        w = int(self._w * 0.60)
        h = int(self._h * 0.08)

        frame[y0 : min(self._h, y0 + h), x0 : min(self._w, x0 + w), :] = fg_v
        return frame

    def _scene_static_regions(self) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        third = max(1, self._w // 3)
        p = int(self._scene_t // 6.0) % 3
        v0 = 0 if p == 0 else 40 if p == 1 else 80
        v1 = 20 if p == 0 else 0 if p == 1 else 40
        v2 = 40 if p == 0 else 20 if p == 1 else 0
        frame[:, 0:third, :] = v0
        frame[:, third : 2 * third, :] = v1
        frame[:, 2 * third : self._w, :] = v2
        return frame

    def _scene_spinner(self) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        cx, cy = int(self._w * 0.60), int(self._h * 0.50)
        r = int(min(self._w, self._h) * 0.10)
        a = float(self._scene_t) * 2.0
        x = cx + int(round(r * math.cos(a)))
        y = cy + int(round(r * math.sin(a)))
        self._dot(frame, x, y, 3, 255)
        return frame

    def _scene_real_subtitles_fade(self, *, fg: str, bg: str) -> np.ndarray:
        bg_v = 0 if bg == "black" else 255
        return np.full((self._h, self._w, 3), bg_v, dtype=np.uint8)

    def _scene_pixel_size_calibration(self) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        per = self._durations[self._scene0] / 3.0
        p = int(self._scene_t // per) % 3
        dot = 2 if p == 0 else 3 if p == 1 else 4
        cx, cy = int(self._w * 0.50), int(self._h * 0.50)
        self._rect(frame, cx - dot, cy - dot, cx + dot, cy + dot, 255)
        return frame

    def _scene_one_tile_sweep(self) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        rows = max(1, int(self._s.grid_rows))
        cols = max(1, int(self._s.grid_cols))
        tiles = rows * cols
        per = self._durations[self._scene0] / float(tiles)
        ti = int(self._scene_t // per)
        ti = 0 if ti < 0 else (tiles - 1 if ti >= tiles else ti)

        r = ti // cols
        c = ti % cols

        x0 = int(round(c * self._w / cols))
        x1 = int(round((c + 1) * self._w / cols))
        y0 = int(round(r * self._h / rows))
        y1 = int(round((r + 1) * self._h / rows))

        self._rect(frame, x0 + 2, y0 + 2, x1 - 2, y1 - 2, 255)
        return frame

    def _scene_slow_pan(self) -> np.ndarray:
        if self._pan_base is None:
            self._pan_base = self._make_static_texture(seed=12_345, contrast=90)

        drift = int(round(self._scene_t * 1.0))
        drift = drift % max(1, self._w)
        base = self._pan_base

        out = np.zeros_like(base)
        out[:, 0 : self._w - drift, :] = base[:, drift:self._w, :]
        out[:, self._w - drift : self._w, :] = base[:, 0:drift, :]
        return out

    def _scene_compression_noise(self, *, mode: str) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        mb = 16
        rng = random.Random(self._noise_phase_seed + int(self._scene_t * 3.0) + (15_000 if mode == "between" else 10_000))
        amp = 1 if mode == "below_no" else 3
        for by in range(0, self._h, mb):
            for bx in range(0, self._w, mb):
                dv = rng.randint(-amp, amp)
                if dv == 0:
                    continue
                block = frame[by : min(self._h, by + mb), bx : min(self._w, bx + mb), :].astype(np.int16)
                frame[by : min(self._h, by + mb), bx : min(self._w, bx + mb), :] = np.clip(block + dv, 0, 255).astype(np.uint8)
        return frame

    def _scene_black_with_blink(self, *, dt: float) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        if self._scene_t >= self._next_blink_s:
            self._blink_on = True
            self._next_blink_s = self._scene_t + 1.5 + self._rng.random() * 4.0

        if self._blink_on:
            x0 = int(self._w * 0.70)
            y0 = int(self._h * 0.05)
            self._rect(frame, x0, y0, x0 + int(self._w * 0.25), y0 + int(self._h * 0.07), 200)
            if self._rng.random() < 0.08:
                self._blink_on = False
        return frame

    def _scene_subtitle_crawl(self, *, dt: float) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        self._crawl_x -= 22.0 * dt
        if self._crawl_x < -float(self._w) * 0.80:
            self._crawl_x = float(self._w)
        return frame

    def _subtitle_crawl_overlay(self) -> SubtitleOverlay:
        return SubtitleOverlay(
            text=self._crawl_text,
            fg_rgb=(255, 255, 255),
            alpha=1.0,
            x_px=int(round(self._crawl_x)),
            y_px=int(round(self._crawl_y)),
        )

    def _subtitle_overlay(self, *, fg: str, bg: str, fade: bool) -> SubtitleOverlay:
        fg_rgb = (255, 255, 255) if fg == "white" else (0, 0, 0)
        if not fade:
            a = 1.0
        else:
            t = self._scene_t % 6.0
            a = (t / 3.0) if t < 3.0 else (1.0 - ((t - 3.0) / 3.0))
            a = _clamp01(a)

        x = int(self._w * 0.12)
        y = int(self._h * 0.82)
        return SubtitleOverlay(text=self._real_sub_text, fg_rgb=fg_rgb, alpha=float(a), x_px=x, y_px=y)

    # ---------------- scenes 19–30 (streaming realism) ----------------

    def _get_static_texture(self) -> np.ndarray:
        if self._static_texture is None:
            self._static_texture = self._make_static_texture(seed=self._static_texture_seed, contrast=100)
        return self._static_texture

    def _make_static_texture(self, *, seed: int, contrast: int) -> np.ndarray:
        rng = random.Random(int(seed))
        base = np.zeros((self._h, self._w, 3), dtype=np.uint8)

        for _ in range(40):
            x0 = rng.randrange(0, self._w)
            y0 = rng.randrange(0, self._h)
            x1 = min(self._w - 1, x0 + rng.randrange(10, max(11, self._w // 3)))
            y1 = min(self._h - 1, y0 + rng.randrange(8, max(9, self._h // 4)))
            v = rng.randrange(0, contrast)
            self._rect(base, x0, y0, x1, y1, v)

        for _ in range(120):
            x = rng.randrange(0, self._w)
            y = rng.randrange(0, self._h)
            v = rng.randrange(0, contrast)
            base[y, x, :] = v

        return base

    def _grain_amp(self) -> int:
        vtag = self._variant_tag(23)
        return 2 if vtag == "v1" else 4 if vtag == "v2" else 7

    def _grain_period_s(self) -> float:
        vtag = self._variant_tag(23)
        return 0.25 if vtag == "v1" else 0.16 if vtag == "v2" else 0.10

    def _mb_amp(self) -> int:
        vtag = self._variant_tag(29)
        return 1 if vtag == "v1" else 2 if vtag == "v2" else 3

    def _mb_period_s(self) -> float:
        vtag = self._variant_tag(29)
        return 0.50 if vtag == "v1" else 0.33 if vtag == "v2" else 0.22

    def _scene_long_black_with_noise(self, *, dt: float) -> np.ndarray:
        vtag = self._variant_tag(19)
        base = 0 if vtag != "v3" else 2
        frame = np.full((self._h, self._w, 3), base, dtype=np.uint8)

        amp = 1 if vtag == "v1" else 2 if vtag == "v2" else 3
        period = 0.45 if vtag == "v1" else 0.30 if vtag == "v2" else 0.18

        if self._scene_t >= self._mb_next_update_s:
            self._mb_next_update_s = self._scene_t + period
            rng = random.Random(self._mb_seed + int(self._scene_t * 1000.0) + 19_000)
            self._mb_cache = (rng.randint(-amp, amp) * np.ones((self._h, self._w, 3), dtype=np.int16))

        if self._mb_cache is not None:
            out = np.clip(frame.astype(np.int16) + self._mb_cache, 0, 255).astype(np.uint8)
            return out

        return frame

    def _scene_logo_bug(self, *, dt: float) -> np.ndarray:
        frame = self._get_static_texture().copy()

        if self._scene_t >= self._logo_next_toggle_s:
            self._logo_next_toggle_s = self._scene_t + (2.5 if self._variant_tag(20) == "v1" else 1.2 if self._variant_tag(20) == "v2" else 0.6)
            if self._rng.random() < (0.35 if self._variant_tag(20) == "v1" else 0.55 if self._variant_tag(20) == "v2" else 0.75):
                self._logo_on = not self._logo_on

        if self._logo_on:
            x0 = int(self._w * 0.83)
            y0 = int(self._h * 0.06)
            self._rect(frame, x0, y0, x0 + int(self._w * 0.12), y0 + int(self._h * 0.08), 240)

            if self._variant_tag(20) != "v1" and self._rng.random() < 0.10:
                self._rect(frame, x0, y0, x0 + int(self._w * 0.12), y0 + int(self._h * 0.08), 210)

        return frame

    def _scene_captions_fade(self, *, dt: float) -> np.ndarray:
        frame = self._get_static_texture().copy()

        vtag = self._variant_tag(21)
        speed = 0.30 if vtag == "v1" else 0.55 if vtag == "v2" else 0.90
        self._cap_alpha += float(self._cap_dir) * speed * dt
        if self._cap_alpha >= 1.0:
            self._cap_alpha = 1.0
            self._cap_dir = -1.0
        if self._cap_alpha <= 0.0:
            self._cap_alpha = 0.0
            self._cap_dir = 1.0

        return frame

    def _captions_overlay(self) -> SubtitleOverlay:
        a = float(_clamp01(self._cap_alpha))
        return SubtitleOverlay(
            text=self._cap_text,
            fg_rgb=(255, 255, 255),
            alpha=a,
            x_px=int(self._w * 0.08),
            y_px=int(self._h * 0.82),
        )

    def _scene_ticker_crawl(self, *, dt: float) -> np.ndarray:
        frame = self._get_static_texture().copy()

        vtag = self._variant_tag(22)
        speed = 30.0 if vtag == "v1" else 60.0 if vtag == "v2" else 95.0
        self._ticker_x -= speed * dt
        if self._ticker_x < -float(self._w) * 1.1:
            self._ticker_x = float(self._w)
        return frame

    def _ticker_overlay(self) -> SubtitleOverlay:
        return SubtitleOverlay(
            text=self._ticker_text,
            fg_rgb=(255, 255, 0),
            alpha=1.0,
            x_px=int(round(self._ticker_x)),
            y_px=int(round(self._h * 0.90)),
        )

    def _scene_film_grain(self, *, dt: float) -> np.ndarray:
        frame = np.full((self._h, self._w, 3), 10, dtype=np.uint8)

        if self._scene_t >= self._grain_next_update_s:
            self._grain_next_update_s = self._scene_t + float(self._grain_period_s())
            rng = np.random.default_rng(int(self._grain_seed) + int(self._scene_t * 1000.0) + 23_000)
            amp = int(self._grain_amp())
            self._grain_cache = rng.integers(-amp, amp + 1, size=(self._h, self._w, 1), dtype=np.int16).repeat(3, axis=2)

        if self._grain_cache is None:
            return frame

        out = np.clip(frame.astype(np.int16) + self._grain_cache, 0, 255).astype(np.uint8)
        return out

    def _scene_brightness_pump(self, *, dt: float) -> np.ndarray:
        base = self._get_static_texture().copy()
        vtag = self._variant_tag(24)

        self._pump_phase += (0.35 if vtag == "v1" else 0.65 if vtag == "v2" else 1.10) * dt
        pump = 0.06 if vtag == "v1" else 0.10 if vtag == "v2" else 0.16
        gain = 1.0 + pump * math.sin(2.0 * math.pi * self._pump_phase)

        out = np.clip(base.astype(np.float32) * float(gain), 0.0, 255.0).astype(np.uint8)
        return out

    def _scene_loading_spinner(self, *, dt: float) -> np.ndarray:
        vtag = self._variant_tag(25)
        frame = self._get_static_texture().copy()

        r = int(min(self._w, self._h) * (0.06 if vtag == "v1" else 0.08 if vtag == "v2" else 0.05))
        speed_rps = 0.6 if vtag == "v1" else 1.2 if vtag == "v2" else 2.2
        dots = 8 if vtag == "v1" else 12 if vtag == "v2" else 10

        self._spinner_phase += float(speed_rps) * (2.0 * math.pi) * dt
        cx, cy = int(self._w * 0.50), int(self._h * 0.55)

        for k in range(dots):
            a = self._spinner_phase + (k * (2.0 * math.pi / float(dots)))
            x = cx + int(round(r * math.cos(a)))
            y = cy + int(round(r * math.sin(a)))
            v = 255 if k == 0 else 120
            self._dot(frame, x, y, 2, v)
        return frame

    def _cut_period_s(self) -> float:
        vtag = self._variant_tag(26)
        return 6.0 if vtag == "v1" else 4.0 if vtag == "v2" else 2.5

    def _cut_spike_s(self) -> float:
        vtag = self._variant_tag(26)
        return 0.35 if vtag == "v1" else 0.45 if vtag == "v2" else 0.55

    def _scene_hard_cuts(self, *, dt: float) -> np.ndarray:
        if self._cut_a is None or self._cut_b is None:
            self._cut_a = self._make_static_texture(seed=self._cut_a_seed, contrast=90).copy()
            self._cut_b = self._make_static_texture(seed=self._cut_b_seed, contrast=120).copy()

        per = float(self._cut_period_s())
        cut_i = int(self._scene_t // per)
        within = float(self._scene_t - (cut_i * per))
        cur = self._cut_a if (cut_i % 2 == 0) else self._cut_b
        out = cur.copy()

        if within < self._cut_spike_s():
            vtag = self._variant_tag(26)
            amp = 10 if vtag == "v1" else 20 if vtag == "v2" else 35
            out = np.clip(out.astype(np.int16) + amp, 0, 255).astype(np.uint8)

        return out

    def _scene_freeze_with_refresh(self, *, dt: float) -> np.ndarray:
        vtag = self._variant_tag(27)
        refresh_period = 6.0 if vtag == "v1" else 3.0 if vtag == "v2" else 1.5
        blocks = 6 if vtag == "v1" else 14 if vtag == "v2" else 28
        amp = 10 if vtag == "v1" else 18 if vtag == "v2" else 28
        mb = 16 if vtag != "v3" else 8

        if self._freeze_base is None:
            self._freeze_base = self._make_static_texture(seed=self._freeze_phase_seed, contrast=95).copy()

        out = self._freeze_base.copy()

        if (self._scene_t - self._freeze_last_refresh_s) >= refresh_period:
            self._freeze_last_refresh_s = float(self._scene_t)
            rng = random.Random(self._noise_phase_seed + int(self._scene_t * 10.0) + 27_000)
            for _ in range(blocks):
                bx = rng.randrange(0, max(1, self._w // mb)) * mb
                by = rng.randrange(0, max(1, self._h // mb)) * mb
                dv = rng.randint(-amp, amp)
                block = out[by : min(self._h, by + mb), bx : min(self._w, bx + mb), :].astype(np.int16)
                out[by : min(self._h, by + mb), bx : min(self._w, bx + mb), :] = np.clip(block + dv, 0, 255).astype(np.uint8)

        return out

    def _scene_detailed_pan(self, *, dt: float) -> np.ndarray:
        vtag = self._variant_tag(28)
        if self._dpan_base is None:
            self._dpan_base = self._make_static_texture(seed=44_444, contrast=160).copy()

        speed = 0.08 if vtag == "v1" else 0.14 if vtag == "v2" else 0.22
        self._dpan_u = float(self._dpan_u + speed * dt)

        dx = int(round((math.sin(2.0 * math.pi * self._dpan_u) * 0.35 + 0.5) * (self._w * 0.12)))
        dy = int(round((math.cos(2.0 * math.pi * self._dpan_u) * 0.35 + 0.5) * (self._h * 0.08)))

        base = self._dpan_base
        out = np.zeros_like(base)
        sx0 = dx % self._w
        sy0 = dy % self._h

        out[:, :, :] = base
        out = np.roll(out, shift=-sx0, axis=1)
        out = np.roll(out, shift=-sy0, axis=0)

        if vtag == "v3" and self._rng.random() < 0.03:
            out = np.clip(out.astype(np.int16) + 10, 0, 255).astype(np.uint8)

        return out

    def _scene_gradient_shimmer(self, *, dt: float) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)

        for x in range(self._w):
            v = int(round(255.0 * float(x) / max(1.0, float(self._w - 1))))
            frame[:, x, :] = v

        if self._scene_t >= self._mb_next_update_s:
            self._mb_next_update_s = self._scene_t + float(self._mb_period_s())
            rng = random.Random(self._mb_seed + int(self._scene_t * 1000.0) + 29_000)
            amp = int(self._mb_amp())
            mb = 16
            shimmer = np.zeros((self._h, self._w, 3), dtype=np.int16)
            for by in range(0, self._h, mb):
                for bx in range(0, self._w, mb):
                    dv = rng.randint(-amp, amp)
                    if dv == 0:
                        continue
                    shimmer[by : min(self._h, by + mb), bx : min(self._w, bx + mb), :] = dv
            self._mb_cache = shimmer

        if self._mb_cache is None:
            return frame

        return np.clip(frame.astype(np.int16) + self._mb_cache, 0, 255).astype(np.uint8)

    def _scene_scrolling_credits(self, *, dt: float) -> np.ndarray:
        vtag = self._variant_tag(30)
        frame = np.full((self._h, self._w, 3), 0, dtype=np.uint8)

        speed = 14.0 if vtag == "v1" else 24.0 if vtag == "v2" else 34.0
        y = int(round(self._h - (self._scene_t * speed) % (self._h + 220)))

        columns = 2 if vtag != "v3" else 3
        col_w = self._w // max(1, columns)
        for c in range(columns):
            x = int(c * col_w + col_w * 0.20)
            for k in range(18):
                yy = y + k * 14
                if 0 <= yy < self._h:
                    self._rect(frame, x, yy, x + int(col_w * 0.55), yy + 2, 220)

        return frame

    # ---------------- small drawing helpers ----------------

    @staticmethod
    def _rect(img: np.ndarray, x0: int, y0: int, x1: int, y1: int, v: int) -> None:
        x0i = max(0, min(int(x0), int(img.shape[1] - 1)))
        x1i = max(0, min(int(x1), int(img.shape[1])))
        y0i = max(0, min(int(y0), int(img.shape[0] - 1)))
        y1i = max(0, min(int(y1), int(img.shape[0])))
        if x1i <= x0i or y1i <= y0i:
            return
        img[y0i:y1i, x0i:x1i, :] = int(max(0, min(255, int(v))))

    @staticmethod
    def _dot(img: np.ndarray, x: int, y: int, r: int, v: int) -> None:
        rr = max(1, int(r))
        xi = int(x)
        yi = int(y)
        for dy in range(-rr, rr + 1):
            yy = yi + dy
            if yy < 0 or yy >= img.shape[0]:
                continue
            for dx in range(-rr, rr + 1):
                xx = xi + dx
                if xx < 0 or xx >= img.shape[1]:
                    continue
                if (dx * dx + dy * dy) <= (rr * rr):
                    img[yy, xx, :] = int(max(0, min(255, int(v))))
