from __future__ import annotations

"""Claude strategy variant.

In the full repository this module reuses the root BaselineStrategy and adds a
freshness-first policy layer.  If the claude directory is copied or executed as
a standalone package and the parent root strategy is absent, it falls back to a
self-contained conservative delivery strategy instead of crashing with
"lizhi_agent/strategy.py not found".
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

from lizhi_agent.actions import ActionBundle, MainAction, MainActionType, SquadAction, SquadActionType, WindowAction, WindowCard, wait
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, ResourceStock, TaskInstance, WindowState
from lizhi_agent.route_planner import RoutePlanner

RUSH_PHASES = {"RUSH", "BANQUET", "ENDGAME", "FINAL", "宫宴冲刺"}
BUSY_STATES = {ConvoyStatus.PROCESSING, ConvoyStatus.VERIFYING, ConvoyStatus.RESTING, ConvoyStatus.FORCED_PASSING, ConvoyStatus.CONTESTING}
MOVING_STATES = {ConvoyStatus.MOVING}
ROUTE_RESOURCE_TYPES = {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL"}


def _load_root_baseline_class():
    root_dir = Path(__file__).resolve().parents[2]
    root_strategy_path = root_dir / "lizhi_agent" / "strategy.py"
    # Avoid loading ourselves when claude is used as the repository root.
    if not root_strategy_path.exists() or root_strategy_path.resolve() == Path(__file__).resolve():
        return None
    spec = importlib.util.spec_from_file_location("_root_lizhi_baseline_strategy", root_strategy_path)
    if spec is None or spec.loader is None:
        return None
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return getattr(module, "BaselineStrategy", None)
    except Exception:
        return None


_RootBaselineStrategy = _load_root_baseline_class()


class _FallbackFreshnessStrategy:
    """Standalone fallback for copied claude directories.

    It is intentionally simple and legal-first: do fixed process, preserve
    freshness, claim nearby tasks/resources, then verify and deliver.
    """

    def __init__(self, player_id: str, config: StrategyConfig, logger: DecisionLogger) -> None:
        self.player_id = player_id
        self.config = config
        self.logger = logger
        self.route_planner = RoutePlanner()
        self._completed_fixed_process_nodes: set[str] = set()
        self._rejected_task_ids: set[str] = set()
        self._rejected_resource_keys: set[tuple[str, str]] = set()
        self._last_station: str | None = None
        self._scout_dispatched: set[str] = set()

    def on_start(self, start_data: dict[str, Any]) -> None:
        self.logger.info("strategy_start", nodes=len(start_data.get("nodes", []) or []), edges=len(start_data.get("edges", []) or []))
        self.logger.info("strategy_variant", variant="CLAUDE_FALLBACK_FRESHNESS", base="self_contained")

    def decide(self, state: GameState) -> ActionBundle:
        self._learn_from_feedback(state)
        if state.me.station != self._last_station and state.me.station is not None:
            self._completed_fixed_process_nodes.discard(state.me.station)
        self._last_station = state.me.station
        bundle, reason = self._decide(state)
        if bundle.squad is not None and bundle.squad.action == SquadActionType.SQUAD_SCOUT and bundle.squad.target:
            self._scout_dispatched.add(bundle.squad.target)
        self.logger.info(
            "decision",
            round=state.frame,
            phase=state.phase,
            station=state.me.station,
            target=state.me.target,
            status=state.me.status.value,
            score=state.me.total_score,
            taskScore=state.me.task_score_base,
            freshness=state.me.freshness,
            reason=reason,
            actions=bundle.to_actions(),
        )
        return bundle

    def _decide(self, state: GameState) -> tuple[ActionBundle, str]:
        me = state.me
        window = self._window_action(state)
        def attach(bundle: ActionBundle) -> ActionBundle:
            if window is None or bundle.window is not None:
                return bundle
            return ActionBundle(main=bundle.main, squad=bundle.squad, window=window, debug=bundle.debug)

        if me.delivered or me.status == ConvoyStatus.DELIVERED:
            return attach(wait("delivered", active=False)), "delivered"
        if me.status in MOVING_STATES or (me.status == ConvoyStatus.WAITING and me.route_edge_id):
            horse = self._horse_action(state)
            if horse is not None:
                return attach(horse), "use_horse_while_moving"
            return attach(wait("moving", active=False)), "moving"
        if me.status in BUSY_STATES or me.current_process is not None:
            return attach(wait("busy", active=False)), "busy"

        ice = self._ice_box_action(state)
        if ice is not None:
            return attach(ice), "use_ice_box"
        if me.station == state.terminal_node:
            if me.verified and me.good_fruit > 0 and me.freshness > 0:
                return attach(ActionBundle(main=MainAction(MainActionType.DELIVER))), "deliver"
            return attach(self._move_to(state, state.gate_node)), "terminal_not_ready"
        if me.station == state.gate_node:
            if not me.verified:
                if state.phase in RUSH_PHASES:
                    return attach(ActionBundle(main=MainAction(MainActionType.VERIFY_GATE, target=state.gate_node))), "verify_gate"
                return attach(wait("wait_gate_rush", active=False)), "wait_gate_rush"
            return attach(self._move_to(state, state.terminal_node)), "gate_to_terminal"

        fixed = self._fixed_process_action(state)
        if fixed is not None:
            return attach(fixed), "fixed_process"
        if self._should_lock_delivery(state):
            return attach(self._move_towards_delivery(state, squad=self._scout_action(state))), "delivery_guard"
        task = self._best_station_task(state)
        if task is not None:
            return attach(ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, target=task.target, task_id=task.id), squad=self._scout_action(state))), f"claim_task:{task.template}:{task.id}"
        res = self._best_station_resource(state)
        if res is not None:
            return attach(ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=res.station, resource_type=res.resource_type), squad=self._scout_action(state))), f"claim_resource:{res.resource_type}"
        res2 = self._best_reachable_resource(state)
        if res2 is not None:
            return attach(self._move_to(state, res2.station, squad=self._scout_action(state))), f"move_to_resource:{res2.resource_type}"
        task2 = self._best_reachable_task(state)
        if task2 is not None:
            return attach(self._move_to(state, task2.target, squad=self._scout_action(state))), f"move_to_task:{task2.template}:{task2.id}"
        return attach(self._move_towards_delivery(state, squad=self._scout_action(state))), "move_towards_delivery"

    def _learn_from_feedback(self, state: GameState) -> None:
        for result in state.action_results:
            if not isinstance(result, dict):
                continue
            accepted = result.get("accepted", result.get("success", True))
            code = str(result.get("code") or result.get("errorCode") or result.get("reason") or "")
            action = str(result.get("action") or result.get("actionType") or "")
            if accepted is False:
                node = result.get("targetNodeId") or result.get("nodeId") or state.me.station
                if action == "CLAIM_TASK" or result.get("taskId"):
                    self._rejected_task_ids.add(str(result.get("taskId")))
                if action == "CLAIM_RESOURCE" or result.get("resourceType"):
                    self._rejected_resource_keys.add((str(node), str(result.get("resourceType"))))
                self.logger.info("action_result", round=state.frame, action=action, accepted=False, code=code, nodeId=node, taskId=result.get("taskId"), resourceType=result.get("resourceType"))
        for event in state.events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event") or event.get("eventType") or event.get("type") or "").upper()
            node = event.get("targetNodeId") or event.get("nodeId") or state.me.station
            if event_type in {"PROCESS_COMPLETE", "FIXED_PROCESS_COMPLETE"} and node:
                self._completed_fixed_process_nodes.add(str(node))
                self.logger.info("feedback_learn", round=state.frame, learned="fixed_process_completed", nodeId=node)

    def _window_action(self, state: GameState) -> WindowAction | None:
        window = state.active_window()
        if window is None:
            return None
        card = self._choose_window_card(state, window)
        self.logger.info("strategy_step", round=state.frame, step="window_card", contestId=window.id, contestType=window.window_type, target=window.target, roundIndex=window.round_index, chosenCard=card.value, windowStyle="fallback", choiceReason="legal_low_regret")
        return WindowAction(window.id, card)

    def _choose_window_card(self, state: GameState, window: WindowState) -> WindowCard:
        high_value = window.window_type in {"GATE", "TASK", "PASS"} or window.resource_type in {"FAST_HORSE", "ICE_BOX"}
        me = state.me
        if high_value and me.guard_points > 0:
            return WindowCard.BING_ZHENG
        if high_value and (me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT")):
            return WindowCard.YAN_DIE
        if high_value and me.freshness >= 85 and me.good_fruit >= 80:
            return WindowCard.XIAN_GONG
        return WindowCard.ABSTAIN

    def _ice_box_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("ICE_BOX"):
            return None
        if me.freshness <= self.config.critical_freshness_threshold or (me.task_score_base >= 60 and me.freshness <= 86) or (me.task_score_base >= 90 and me.freshness <= 90):
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="fallback_freshness_guard", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        return None

    def _horse_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.has_resource("FAST_HORSE"):
            self.logger.info("resource_use", round=state.frame, resourceType="FAST_HORSE", reason="moving_speed", freshness=me.freshness)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if me.has_resource("SHORT_HORSE"):
            self.logger.info("resource_use", round=state.frame, resourceType="SHORT_HORSE", reason="moving_speed", freshness=me.freshness)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _fixed_process_action(self, state: GameState) -> ActionBundle | None:
        station_id = state.me.station
        station = state.station(station_id)
        if station_id is None or station is None or not station.process_type:
            return None
        if station_id in self._completed_fixed_process_nodes:
            return None
        self.logger.info("fixed_process_eval", round=state.frame, station=station_id, processType=station.process_type, action="PROCESS", reason="fallback_required")
        return ActionBundle(main=MainAction(MainActionType.PROCESS, target=station_id))

    def _should_lock_delivery(self, state: GameState) -> bool:
        me = state.me
        if me.task_score_base < self.config.target_task_score:
            return False
        if state.phase in RUSH_PHASES:
            return True
        if me.task_score_base >= self.config.greed_task_score:
            return True
        if me.freshness < 86 or me.good_fruit < 88:
            return True
        if me.task_score_base >= self.config.competitive_task_score and (me.freshness < 92 or me.good_fruit < 94):
            return True
        return self._need_endgame(state)

    def _need_endgame(self, state: GameState) -> bool:
        me = state.me
        target = state.terminal_node if me.verified else state.gate_node
        if me.station is None:
            return False
        cost = self.route_planner.estimate_frames(state, me.station, target)
        return state.turns_left <= cost + self.config.endgame_buffer_frames

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        tasks = [t for t in state.station_tasks(state.me.station) if t.available_for(state.player_id) and t.id not in self._rejected_task_ids]
        if not tasks:
            return None
        if state.me.task_score_base >= self.config.target_task_score and (state.me.freshness < 92 or state.me.good_fruit < 92):
            return None
        return max(tasks, key=lambda t: (100 if state.me.task_score_base < self.config.target_task_score else 0) + t.score - t.process_frames)

    def _best_reachable_task(self, state: GameState) -> TaskInstance | None:
        me = state.me
        if me.station is None or self._should_lock_delivery(state):
            return None
        direct = self.route_planner.estimate_frames(state, me.station, state.gate_node)
        best: tuple[int, TaskInstance] | None = None
        for task in state.tasks:
            if task.id in self._rejected_task_ids or not task.available_for(state.player_id) or task.score <= 0:
                continue
            if me.task_score_base >= self.config.target_task_score and (me.freshness < 92 or me.good_fruit < 92):
                continue
            detour = self.route_planner.estimate_frames(state, me.station, task.target) + task.process_frames + self.route_planner.estimate_frames(state, task.target, state.gate_node) - direct
            max_detour = self.config.max_task_detour_frames if me.task_score_base < self.config.target_task_score else min(self.config.max_competitive_task_detour_frames, 18)
            if detour <= max_detour:
                score = task.score * 4 - detour
                if best is None or score > best[0]:
                    best = (score, task)
        return best[1] if best else None

    def _best_station_resource(self, state: GameState) -> ResourceStock | None:
        stocks = [s for s in state.station_resources(state.me.station) if (s.station, s.resource_type) not in self._rejected_resource_keys and s.resource_type in self.config.resource_priority]
        if not stocks:
            return None
        if self._should_lock_delivery(state):
            stocks = [s for s in stocks if s.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL"}]
        if not stocks:
            return None
        return max(stocks, key=lambda s: self._resource_value(state, s, 0))

    def _best_reachable_resource(self, state: GameState) -> ResourceStock | None:
        me = state.me
        if me.station is None or self._should_lock_delivery(state):
            return None
        direct = self.route_planner.estimate_frames(state, me.station, state.gate_node)
        best: tuple[int, ResourceStock] | None = None
        for stock in state.resources:
            if stock.resource_type not in ROUTE_RESOURCE_TYPES or (stock.station, stock.resource_type) in self._rejected_resource_keys:
                continue
            detour = self.route_planner.estimate_frames(state, me.station, stock.station) + stock.claim_frames + self.route_planner.estimate_frames(state, stock.station, state.gate_node) - direct
            cap = 6 if me.task_score_base >= self.config.target_task_score else self.config.max_valuable_resource_detour_frames
            if detour <= cap:
                value = self._resource_value(state, stock, detour)
                if best is None or value > best[0]:
                    best = (value, stock)
        return best[1] if best else None

    def _resource_value(self, state: GameState, stock: ResourceStock, detour: int) -> int:
        priority = {name: i for i, name in enumerate(self.config.resource_priority)}
        base = 100 - priority.get(stock.resource_type, 999) * 8
        me = state.me
        if stock.resource_type == "ICE_BOX":
            base += 45 if me.freshness <= 88 else 20
        elif stock.resource_type == "FAST_HORSE":
            base += 35
        elif stock.resource_type == "SHORT_HORSE":
            base += 22
        elif stock.resource_type == "INTEL":
            base += 15
        return base - max(0, detour * 2)

    def _scout_action(self, state: GameState) -> SquadAction | None:
        me = state.me
        if me.squad_available <= 0 or me.station is None or state.phase in RUSH_PHASES:
            return None
        target = state.gate_node if me.task_score_base >= self.config.target_task_score and not me.verified else None
        if target is None:
            task = self._best_reachable_task(state)
            if task is not None:
                target = task.target
        if target is None:
            res = self._best_reachable_resource(state)
            if res is not None:
                target = res.station
        if target and target not in self._scout_dispatched and target != me.station:
            self.logger.info("squad_eval", round=state.frame, action="SQUAD_SCOUT", target=target, reason="fallback_valuable_scout")
            return SquadAction(SquadActionType.SQUAD_SCOUT, target)
        return None

    def _move_towards_delivery(self, state: GameState, *, squad: SquadAction | None = None) -> ActionBundle:
        return self._move_to(state, state.terminal_node if state.me.verified else state.gate_node, squad=squad)

    def _move_to(self, state: GameState, target: str | None, *, squad: SquadAction | None = None) -> ActionBundle:
        if target is None or state.me.station is None or state.me.station == target:
            return ActionBundle(squad=squad)
        next_hop = self.route_planner.next_hop(state, state.me.station, target)
        if next_hop is None:
            return ActionBundle(squad=squad)
        return ActionBundle(main=MainAction(MainActionType.MOVE, target=next_hop), squad=squad)


if _RootBaselineStrategy is not None:
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
            if state.phase in RUSH_PHASES:
                return True
            if self._need_endgame(state) or self._opponent_pressure(state):
                return True
            if me.freshness < 86 or me.good_fruit < 88:
                return True
            if me.task_score_base >= self.config.competitive_task_score and (me.freshness < 92 or me.good_fruit < 94):
                return True
            return False

        def _freshness_action(self, state: GameState) -> ActionBundle | None:
            me = state.me
            if not me.has_resource("ICE_BOX"):
                return None
            if me.freshness <= self.config.critical_freshness_threshold:
                self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="claude_critical_freshness", freshness=me.freshness)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
            if me.task_score_base >= self.config.target_task_score and me.freshness <= 90:
                self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="claude_protect_delivery_quality", freshness=me.freshness, taskScore=me.task_score_base)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
            if me.task_score_base >= 60 and me.freshness <= 86:
                self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="claude_midgame_quality_guard", freshness=me.freshness, taskScore=me.task_score_base)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
            if self._need_endgame(state) and me.freshness <= 94:
                self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="claude_endgame_preload", freshness=me.freshness)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
            return None

        def _best_station_task(self, state: GameState) -> TaskInstance | None:
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
            if state.gate_node in forbidden:
                forbidden = set(forbidden)
                forbidden.discard(state.gate_node)
            return forbidden

        def _scout_target_value(self, state: GameState, node: str, objective: str) -> tuple[int, str]:
            value, reason = super()._scout_target_value(state, node, objective)
            if node == state.gate_node:
                if state.me.task_score_base >= self.config.target_task_score or self._need_endgame(state) or state.phase in RUSH_PHASES:
                    return max(value, 95), "verify_gate"
            return value, reason
else:
    FreshnessFirstStrategy = _FallbackFreshnessStrategy


BaselineStrategy = FreshnessFirstStrategy
RoadMasterStrategy = FreshnessFirstStrategy
