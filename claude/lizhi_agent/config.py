from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    target_task_score: int = 90
    # Claude variant: still freshness-first, but no longer under-scores tasks.
    # The previous 125/145 cap was too conservative against stronger bots.
    competitive_task_score: int = 145
    greed_task_score: int = 165
    endgame_buffer_frames: int = 55
    max_task_detour_frames: int = 16
    max_competitive_task_detour_frames: int = 22
    max_resource_detour_frames: int = 7
    max_valuable_resource_detour_frames: int = 14
    station_stall_frames: int = 14
    station_escape_frames: int = 30
    object_cooldown_frames: int = 30
    process_start_grace_frames: int = 2
    max_window_rounds_before_abstain: int = 2
    opening_window_mix_frames: int = 120
    low_freshness_threshold: float = 84.0
    critical_freshness_threshold: float = 66.0
    resource_priority: tuple[str, ...] = (
        "ICE_BOX",
        "FAST_HORSE",
        "SHORT_HORSE",
        "INTEL",
        "PASS_TOKEN",
        "OFFICIAL_PERMIT",
        "BOAT_RIGHT",
    )

    @staticmethod
    def default() -> "StrategyConfig":
        return StrategyConfig()
