from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    target_task_score: int = 90
    competitive_task_score: int = 130
    greed_task_score: int = 150
    endgame_buffer_frames: int = 45
    max_task_detour_frames: int = 18
    max_competitive_task_detour_frames: int = 34
    max_resource_detour_frames: int = 8
    max_valuable_resource_detour_frames: int = 18
    station_stall_frames: int = 18
    station_escape_frames: int = 36
    object_cooldown_frames: int = 30
    max_window_rounds_before_abstain: int = 2
    opening_window_mix_frames: int = 120
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
    # Scout only mid/late route information nodes. Do not put gate/terminal
    # here: S14/S15 are mandatory and usually already known, so scouting them
    # on round 1 wastes a scarce squad action.
    scout_targets: tuple[str, ...] = ("S08", "S10", "S11", "S13", "S07")

    @staticmethod
    def default() -> "StrategyConfig":
        return StrategyConfig()
