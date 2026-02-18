# testdata/profile.py
from __future__ import annotations

from dataclasses import dataclass

ProfileName = str  # "fast" | "default" | "slow"


@dataclass(frozen=True)
class TestDataProfile:
    name: ProfileName
    scale_existing_1_to_9: float
    scale_new_10_plus: float
    tile_sweep_seconds_per_tile: float

    @staticmethod
    def from_name(name: ProfileName) -> "TestDataProfile":
        n = name.lower().strip()
        if n == "fast":
            return TestDataProfile(
                name="fast",
                scale_existing_1_to_9=0.25,   # 60s -> 15s, 30s -> 7.5s (rounded in engine)
                scale_new_10_plus=0.40,
                tile_sweep_seconds_per_tile=1.0,
            )
        if n == "slow":
            return TestDataProfile(
                name="slow",
                scale_existing_1_to_9=2.0,    # 60s -> 120s
                scale_new_10_plus=2.0,
                tile_sweep_seconds_per_tile=5.0,
            )
        return TestDataProfile(
            name="default",
            scale_existing_1_to_9=1.0,
            scale_new_10_plus=1.0,
            tile_sweep_seconds_per_tile=2.5,
        )
