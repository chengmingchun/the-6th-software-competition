from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    target_task_score: int = 90
    greed_task_score: int = 110
    endgame_buffer_frames: int = 45
    max_task_detour_frames: int = 18
    max_resource_detour_frames: int = 8
    low_freshness_threshold: float = 75.0
    critical_freshness_threshold: float = 55.0
    resource_priority: tuple[str, ...] = (
        "ICE_BOX",
        "FAST_HORSE",
        "SHORT_HORSE",
        "INTEL",
        "PASS_TOKEN",
        "OFFICIAL_PERMIT",
        "BOAT_RIGHT",
    )
    scout_targets: tuple[str, ...] = ("S14", "S13", "S11", "S10", "S08")

    @staticmethod
    def default() -> "StrategyConfig":
        return StrategyConfig()
