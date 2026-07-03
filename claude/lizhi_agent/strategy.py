from __future__ import annotations

"""Claude strategy variant.

Full-repo mode: reuse root BaselineStrategy and add a freshness/score balanced
policy layer.  Standalone mode: if the parent root strategy is absent, fall back
to a compact self-contained legal-first strategy instead of crashing.
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
WINDOW_TERMINAL_STATUSES = {"SUPPRESSED", "RESOLVED", "FINISHED", "FINISH", "ENDED", "END", "CLOSED", "COMPLETED", "COMPLETE", "SETTLED"}


def _load_root_baseline_class():
    root_dir = Path(__file__).resolve().parents[2]
    root_strategy_path = root_dir / "lizhi_agent" / "strategy.py"
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
    """Standalone fallback for copied claude directories."""

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
        self.logger.info("strategy_variant", variant="CLAUDE_FALLBACK_PURPOSEFUL", base="self_contained")

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
        intel = self._intel_action(state)
        if intel is not None:
            return attach(intel), "use_intel"
        if me.station == state.terminal_node:
            if me.verified and me.good_fruit > 0 and me.freshness > 0:
                return attach(ActionBundle(main=MainAction(MainActionType.DELIVER))), "deliver"
            return attach(self._move_to(state, state.gate_node)), "terminal_not_ready"
        if me.station == state.gate_node:
            if not me.verified:
                if state.phase in RUSH_PHASES:
                    return attach(ActionBundle(main=MainAction(MainActionType.VERIFY_GATE, target=state.gate_node, rush_tactic="BREAK_ORDER" if me.rush_tactic_used_count == 0 else None))), "verify_gate"
                return attach(wait("wait_gate_rush", active=False)), "wait_gate_rush"
            return attach(self._move_to(state, state.terminal_node)), "gate_to_terminal"

        fixed = self._fixed_process_action(state)
        if fixed is not None:
            return attach(fixed), "fixed_process"
        pre = self._pre_move_resource_action(state)
        if pre is not None:
            return attach(pre), "use_route_resource"
        if self._should_lock_delivery(state):
            return attach(self._move_towards_delivery(state, squad=self._scout_action(state))), "delivery_guard"
        task = self._best_station_task(state)
        if task is not None:
            return attach(ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, target=task.target, task_id=task.id), squad=self._scout_action(state))), f"claim_task:{task.template}:{task.id}"
        res = self._best_station_resource(state)
        if res is not None:
            return attach(ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=res.station, resource_type=res.resource_type), squad=self._scout_action(state))), f"claim_resource:{res.resource_type}"
        task2 = self._best_reachable_task(state)
        if task2 is not None:
            return attach(self._move_to(state, task2.target, squad=self._scout_action(state))), f"move_to_task:{task2.template}:{task2.id}"
        res2 = self._best_reachable_resource(state)
        if res2 is not None:
            return attach(self._move_to(state, res2.station, squad=self._scout_action(state))), f"move_to_resource:{res2.resource_type}"
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
        self.logger.info("strategy_step", round=state.frame, step="window_card", contestId=window.id, contestType=window.window_type, target=window.target, roundIndex=window.round_index, chosenCard=card.value, windowStyle="targeted", choiceReason="fallback_targeted")
        return WindowAction(window.id, card)

    def _choose_window_card(self, state: GameState, window: WindowState) -> WindowCard:
        high = _is_high_value_window(state, window)
        me = state.me
        if high and me.guard_points > 0:
            return WindowCard.BING_ZHENG
        if high and (me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT")):
            return WindowCard.YAN_DIE
        if high and (me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE")):
            return WindowCard.QIANG_XING
        if high and me.freshness >= 76 and me.good_fruit >= 70:
            return WindowCard.XIAN_GONG
        return WindowCard.ABSTAIN

    def _ice_box_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("ICE_BOX"):
            return None
        if me.freshness <= self.config.critical_freshness_threshold or (me.task_score_base >= 60 and me.freshness <= 88) or (me.task_score_base >= 90 and me.freshness <= 92):
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="purposeful_freshness_guard", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        return None

    def _horse_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        if me.has_resource("FAST_HORSE"):
            self.logger.info("resource_use", round=state.frame, resourceType="FAST_HORSE", reason="moving_speed")
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if me.has_resource("SHORT_HORSE"):
            self.logger.info("resource_use", round=state.frame, resourceType="SHORT_HORSE", reason="moving_speed")
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _pre_move_resource_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.station is None or me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        target = state.terminal_node if me.verified else state.gate_node
        remaining = self.route_planner.estimate_frames(state, me.station, target)
        if remaining >= 4 and me.has_resource("FAST_HORSE"):
            self.logger.info("resource_use", round=state.frame, resourceType="FAST_HORSE", reason="pre_move_route", target=target, remainingCost=remaining)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if remaining >= 3 and me.has_resource("SHORT_HORSE"):
            self.logger.info("resource_use", round=state.frame, resourceType="SHORT_HORSE", reason="pre_move_route", target=target, remainingCost=remaining)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _intel_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("INTEL") or me.station is None:
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
        if target and target != me.station:
            self.logger.info("resource_use", round=state.frame, resourceType="INTEL", reason="purposeful_scout", target=target)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, target=target, resource_type="INTEL"))
        return None

    def _fixed_process_action(self, state: GameState) -> ActionBundle | None:
        station_id = state.me.station
        station = state.station(station_id)
        if station_id is None or station is None or not station.process_type or station.process_type == "VERIFY":
            return None
        if station_id in self._completed_fixed_process_nodes:
            return None
        self.logger.info("fixed_process_eval", round=state.frame, station=station_id, processType=station.process_type, action="PROCESS", reason="required")
        return ActionBundle(main=MainAction(MainActionType.PROCESS, target=station_id))

    def _should_lock_delivery(self, state: GameState) -> bool:
        me = state.me
        if me.task_score_base < self.config.target_task_score:
            return False
        if state.phase in RUSH_PHASES or self._need_endgame(state):
            return True
        if me.task_score_base >= self.config.greed_task_score:
            return True
        if me.freshness < 82 or me.good_fruit < 84:
            return True
        if me.task_score_base >= self.config.competitive_task_score and (me.freshness < 89 or me.good_fruit < 90):
            return True
        return False

    def _need_endgame(self, state: GameState) -> bool:
        me = state.me
        if state.phase in RUSH_PHASES:
            return True
        if me.station is None:
            return False
        target = state.terminal_node if me.verified else state.gate_node
        cost = self.route_planner.estimate_frames(state, me.station, target)
        return state.turns_left <= cost + self.config.endgame_buffer_frames

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        tasks = [t for t in state.station_tasks(state.me.station) if t.available_for(state.player_id) and t.id not in self._rejected_task_ids]
        if not tasks:
            return None
        if state.me.task_score_base >= self.config.target_task_score and (self._should_lock_delivery(state) or state.me.freshness < 86 or state.me.good_fruit < 82):
            return None
        return max(tasks, key=lambda t: (120 if state.me.task_score_base < self.config.target_task_score else 0) + t.score * 3 - t.process_frames)

    def _best_reachable_task(self, state: GameState) -> TaskInstance | None:
        me = state.me
        if me.station is None or self._should_lock_delivery(state):
            return None
        direct = self.route_planner.estimate_frames(state, me.station, state.gate_node)
        best: tuple[int, TaskInstance] | None = None
        for task in state.tasks:
            if task.id in self._rejected_task_ids or not task.available_for(state.player_id) or task.score <= 0:
                continue
            if me.task_score_base >= self.config.target_task_score and (me.freshness < 88 or me.good_fruit < 84):
                continue
            detour = self.route_planner.estimate_frames(state, me.station, task.target) + task.process_frames + self.route_planner.estimate_frames(state, task.target, state.gate_node) - direct
            cap = self.config.max_task_detour_frames if me.task_score_base < self.config.target_task_score else self.config.max_competitive_task_detour_frames
            if detour <= cap:
                value = task.score * 4 - detour + (35 if task.score >= 30 else 0) + (25 if task.template == "T04" else 0)
                if best is None or value > best[0]:
                    best = (value, task)
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
        if me.station is None:
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
            base += 60 if me.freshness <= 90 else 25
        elif stock.resource_type == "FAST_HORSE":
            base += 45
        elif stock.resource_type == "SHORT_HORSE":
            base += 30
        elif stock.resource_type == "INTEL":
            base += 25
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
            self.logger.info("squad_eval", round=state.frame, action="SQUAD_SCOUT", target=target, reason="purposeful_scout")
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
        """Root baseline plus purposeful score/freshness overrides."""

        def on_start(self, start_data: dict[str, Any]) -> None:
            super().on_start(start_data)
            self.logger.info("strategy_variant", variant="CLAUDE_PURPOSEFUL", base="root_baseline", targetTaskScore=self.config.target_task_score, competitiveTaskScore=self.config.competitive_task_score, greedTaskScore=self.config.greed_task_score)

        def _optional_window_action(self, state: GameState) -> tuple[WindowAction | None, str | None]:
            window = state.active_window()
            if window is None:
                return None, None
            object_key = self._window_object_key(window) or f"WINDOW:{window.id}"
            status = str(window.status or "").upper()
            seen = self._window_seen.get(object_key, 0) + 1
            self._window_seen[object_key] = seen
            if object_key in self._suppressed_window_keys or status in WINDOW_TERMINAL_STATUSES:
                return None, None
            if seen > 3 or window.round_index > 3:
                self._suppress_window(object_key, state, f"window_repeated:{seen}:roundIndex={window.round_index}")
                return None, None
            card, style, reason = self._purposeful_window_card(state, window)
            self.logger.info("strategy_step", step="window_card", contestId=window.id, contestType=window.window_type, target=window.target, resourceType=window.resource_type, taskId=window.task_id, roundIndex=window.round_index, chosenCard=card.value, windowStyle=style, choiceReason=reason)
            return WindowAction(window.id, card), f"window:{window.window_type}:{card.value}:{reason}"

        def _purposeful_window_card(self, state: GameState, window: WindowState) -> tuple[WindowCard, str, str]:
            me = state.me
            high = _is_high_value_window(state, window)
            if not high:
                return WindowCard.ABSTAIN, "LOW_VALUE", "低价值窗口弃权省资源"
            # 高价值窗口不能无脑弃权：按资源损耗从低到高使用。
            if me.guard_points > 0:
                return WindowCard.BING_ZHENG, "TARGETED_GUARD", "高价值窗口优先用护卫点"
            if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
                return WindowCard.YAN_DIE, "TARGETED_PASS", "通行类资源反制/争抢"
            if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
                return WindowCard.QIANG_XING, "TARGETED_SPEED", "速度资源争抢关键窗口"
            if me.freshness >= 76 and me.good_fruit >= 70:
                return WindowCard.XIAN_GONG, "TARGETED_FRUIT", "关键窗口用鲜果换收益"
            return WindowCard.ABSTAIN, "RESOURCE_EMPTY", "资源/果况不足才弃权"

        def _should_lock_delivery(self, state: GameState) -> bool:
            me = state.me
            if me.task_score_base < self.config.target_task_score:
                return False
            if state.phase in RUSH_PHASES:
                return True
            if self._need_endgame(state) or self._opponent_pressure(state):
                return True
            if me.task_score_base >= self.config.greed_task_score:
                return True
            if me.freshness < 82 or me.good_fruit < 84:
                return True
            if me.task_score_base >= self.config.competitive_task_score and (me.freshness < 89 or me.good_fruit < 90):
                return True
            return False

        def _freshness_action(self, state: GameState) -> ActionBundle | None:
            me = state.me
            if not me.has_resource("ICE_BOX"):
                return None
            if me.freshness <= self.config.critical_freshness_threshold:
                self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="critical_freshness", freshness=me.freshness)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
            if me.task_score_base >= 60 and me.freshness <= 88:
                self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="protect_midgame_quality", freshness=me.freshness, taskScore=me.task_score_base)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
            if me.task_score_base >= self.config.target_task_score and me.freshness <= 92:
                self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="protect_delivery_quality", freshness=me.freshness, taskScore=me.task_score_base)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
            return None

        def _pre_move_resource_action(self, state: GameState) -> ActionBundle | None:
            me = state.me
            if me.status not in getattr(sys.modules.get("_root_lizhi_baseline_strategy"), "PLANNING_STATES", {ConvoyStatus.IDLE, ConvoyStatus.WAITING, ConvoyStatus.UNKNOWN, ConvoyStatus.COST_BANKRUPT}) or me.station is None:
                return None
            if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
                return None
            target = self._current_route_objective(state)
            remaining = self.route_planner.estimate_frames(state, me.station, target)
            if remaining >= 4 and me.has_resource("FAST_HORSE"):
                self.logger.info("resource_use", round=state.frame, resourceType="FAST_HORSE", reason="purposeful_pre_move", target=target, remainingCost=remaining)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
            if remaining >= 3 and me.has_resource("SHORT_HORSE"):
                self.logger.info("resource_use", round=state.frame, resourceType="SHORT_HORSE", reason="purposeful_pre_move", target=target, remainingCost=remaining)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
            return None

        def _moving_horse_action(self, state: GameState) -> ActionBundle | None:
            me = state.me
            if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
                return None
            if me.has_resource("FAST_HORSE"):
                self.logger.info("resource_use", round=state.frame, resourceType="FAST_HORSE", reason="purposeful_moving_speedup")
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
            if me.has_resource("SHORT_HORSE"):
                self.logger.info("resource_use", round=state.frame, resourceType="SHORT_HORSE", reason="purposeful_moving_speedup")
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
            return None

        def _intel_action(self, state: GameState) -> ActionBundle | None:
            me = state.me
            if not me.has_resource("INTEL") or me.status not in getattr(sys.modules.get("_root_lizhi_baseline_strategy"), "PLANNING_STATES", {ConvoyStatus.IDLE, ConvoyStatus.WAITING, ConvoyStatus.UNKNOWN, ConvoyStatus.COST_BANKRUPT}) or me.station is None:
                return None
            target = state.gate_node if me.task_score_base >= self.config.target_task_score and not me.verified else None
            if target is None:
                target = self._intel_target(state)
            if target is not None and target != me.station:
                self.logger.info("resource_use", round=state.frame, resourceType="INTEL", reason="purposeful_intel", target=target)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, target=target, resource_type="INTEL"))
            return None

        def _best_station_task(self, state: GameState) -> TaskInstance | None:
            if state.me.task_score_base >= self.config.target_task_score:
                if self._should_lock_delivery(state):
                    return None
                if state.me.freshness < 86 or state.me.good_fruit < 82:
                    return None
            return super()._best_station_task(state)

        def _best_reachable_task(self, state: GameState, *, exclude_current_station: bool = False) -> TaskInstance | None:
            me = state.me
            if me.task_score_base >= self.config.target_task_score:
                if self._should_lock_delivery(state):
                    return None
                if me.freshness < 88 or me.good_fruit < 84:
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
                    return None
                _, chosen, _ = max(candidates, key=lambda item: item[0])
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


def _is_high_value_window(state: GameState, window: WindowState) -> bool:
    if window.target in {state.gate_node, state.terminal_node}:
        return True
    if window.window_type in {"GATE", "TASK", "PASS", "PROCESS", "FIXED_PROCESS"}:
        return True
    if window.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL", "PASS_TOKEN", "OFFICIAL_PERMIT"}:
        return True
    if window.task_id:
        return True
    return False


BaselineStrategy = FreshnessFirstStrategy
RoadMasterStrategy = FreshnessFirstStrategy
