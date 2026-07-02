from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    target_task_score: int = 90
    greed_task_score: int = 110
    endgame_buffer_frames: int = 35
    low_freshness_threshold: float = 75.0
    resource_priority: tuple[str, ...] = (
        "INTEL",
        "ICE_BOX",
        "FAST_HORSE",
        "SHORT_HORSE",
        "PASS_TOKEN",
        "OFFICIAL_PERMIT",
        "BOAT_RIGHT",
    )
    route_targets: tuple[str, ...] = ("S14", "S15")

    @staticmethod
    def default() -> "StrategyConfig":
        return StrategyConfig()
