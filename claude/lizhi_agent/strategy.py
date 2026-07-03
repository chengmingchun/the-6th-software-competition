from __future__ import annotations

"""Claude strategy variant.

This module intentionally reuses the root BaselineStrategy implementation so
Claude keeps inheriting the battle-tested baseline fixes.  The subclass below
adds a small freshness-first policy layer: earlier delivery lock, earlier
ICE_BOX usage, tighter post-90 detours, and S14 scout/value support.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

from lizhi_agent.actions import ActionBundle, MainAction, MainActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.models import GameState, ResourceStock, TaskInstance

_ROOT_DIR = Path(__file__).resolve().parents[2]
_ROOT_STRATEGY_PATH = _ROOT_DIR / "lizhi_agent" / "strategy.py"

_spec = importlib.util.spec_from_file_location("_root_lizhi_baseline_strategy", _ROOT_STRATEGY_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load root baseline strategy from {_ROOT_STRATEGY_PATH}")
_root_strategy = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _root_strategy
_spec.loader.exec_module(_root_strategy)

_RootBaselineStrategy = _root_strategy.BaselineStrategy
_RUSH_PHASES = getattr(_root_strategy, "RUSH_PHASES", {"RUSH", "BANQUET", "ENDGAME", "FINAL", "宫宴冲刺"})


class FreshnessFirstStrategy(_RootBaselineStrategy):
    """Root baseline plus freshness-first guards for the Claude experiment."""

    def on_start(self, start_data: dict[str, Any]) -> None:
        super().on_start(start_data)
        self.logger.info(
            "strategy_variant",
            variant="CLAUDE_FRESHNESS_FIRST",
            base="root_baseline",
            targetTaskScore=self.config.target_task_score,
            competitiveTaskScore=self.config.competitive_task_score,
            greedTaskScore=self.config.greed_task_score,
        )

    def _should_lock_delivery(self, state: GameState) -> bool:
        me = state.me
        if me.task_score_base >= self.config.greed_task_score:
            return True
        if me.task_score_base < self.config.target_task_score:
            return False
        if state.phase in _RUSH_PHASES:
            return True
        if self._need_endgame(state) or self._opponent_pressure(state):
            return True
        # Preserve delivery quality: freshness and good fruit are large score terms.
        if me.freshness < 86 or me.good_fruit < 88:
            return True
        # A comfortable 120+ score is usually enough unless quality is excellent.
        if me.task_score_base >= self.config.competitive_task_score and (me.freshness < 92 or me.good_fruit < 94):
            return True
        return False

    def _freshness_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("ICE_BOX"):
            return None
        if me.freshness <= self.config.critical_freshness_threshold:
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="claude_critical_freshness", freshness=me.freshness)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.task_score_base >= self.config.target_task_score and me.freshness <= 90:
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="claude_protect_delivery_quality", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.task_score_base >= 60 and me.freshness <= 86:
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="claude_midgame_quality_guard", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if self._need_endgame(state) and me.freshness <= 94:
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="claude_endgame_preload", freshness=me.freshness)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        return None

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        # After the score floor, do not spend delivery quality on ordinary tasks.
        if state.me.task_score_base >= self.config.target_task_score:
            if self._should_lock_delivery(state):
                return None
            if state.me.freshness < 92 or state.me.good_fruit < 92:
                return None
        return super()._best_station_task(state)

    def _best_reachable_task(self, state: GameState, *, exclude_current_station: bool = False) -> TaskInstance | None:
        me = state.me
        if me.task_score_base >= self.config.target_task_score:
            if self._should_lock_delivery(state):
                return None
            if me.freshness < 92 or me.good_fruit < 92:
                return None
        return super()._best_reachable_task(state, exclude_current_station=exclude_current_station)

    def _best_reachable_resource(self, state: GameState, *, exclude_current_station: bool = False) -> ResourceStock | None:
        me = state.me
        if me.task_score_base >= self.config.target_task_score:
            # In quality-delivery mode only chase very nearby preservation/speed resources.
            if self._should_lock_delivery(state):
                return None
            direct = self.route_planner.estimate_frames(state, me.station, state.gate_node) if me.station else 10**9
            candidates: list[tuple[int, ResourceStock, int]] = []
            for stock in state.resources:
                if exclude_current_station and stock.station == me.station:
                    continue
                if stock.resource_type not in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL"}:
                    continue
                if (stock.station, stock.resource_type) in self._rejected_resource_keys:
                    continue
                if self._is_object_on_cooldown(state, self._resource_object_key(stock.station, stock.resource_type)):
                    continue
                if me.station is None:
                    continue
                to_res = self.route_planner.estimate_frames(state, me.station, stock.station)
                to_gate = self.route_planner.estimate_frames(state, stock.station, state.gate_node)
                detour = to_res + stock.claim_frames + to_gate - direct
                if detour <= 6:
                    candidates.append((self._resource_value(state, stock, detour=detour), stock, detour))
            if not candidates:
                self.logger.info("resource_eval_reachable", directToGate=direct, candidates=[], reason="claude_quality_delivery_detour_cap")
                return None
            _, chosen, detour = max(candidates, key=lambda item: item[0])
            self.logger.info("resource_eval_reachable", chosen=chosen.resource_type, chosenStation=chosen.station, chosenDetour=detour, reason="claude_quality_delivery_resource")
            return chosen
        return super()._best_reachable_resource(state, exclude_current_station=exclude_current_station)

    def _scout_forbidden(self, state: GameState) -> set[str]:
        forbidden = super()._scout_forbidden(state)
        # Root baseline forbids gate/terminal.  S14 scout is valuable because
        # VERIFY_GATE consumes scout markers in the calibrated simulator/rules.
        if state.gate_node in forbidden:
            forbidden = set(forbidden)
            forbidden.discard(state.gate_node)
        return forbidden

    def _scout_target_value(self, state: GameState, node: str, objective: str) -> tuple[int, str]:
        value, reason = super()._scout_target_value(state, node, objective)
        if node == state.gate_node:
            if state.me.task_score_base >= self.config.target_task_score or self._need_endgame(state) or state.phase in _RUSH_PHASES:
                return max(value, 95), "verify_gate"
        return value, reason


# Keep all existing launchers/tests simple: Claude's BaselineStrategy name now
# points to the freshness-first baseline-derived variant.
BaselineStrategy = FreshnessFirstStrategy
RoadMasterStrategy = FreshnessFirstStrategy
