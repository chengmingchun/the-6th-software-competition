from __future__ import annotations

"""Self-contained Claude strategy.

This version intentionally does not dynamically import the parent repository's
root strategy.  The previous dynamic/fallback hybrid was fragile when the
`claude` directory was run as an independent bot.  It could also call a
nonexistent RoutePlanner.next_hop() method and then fall back to empty actions,
which looked like waiting forever at S01.

Design goals:
- Always produce a legal movement when the convoy is idle and a path exists.
- Keep a compact freshness-first score plan: 90 floor, 145 competitive, 165 cap.
- Use resources intentionally instead of hoarding them.
- Contest high-value windows, abstain low-value windows.
- Stay self-contained for local launch, tournament runner, and submission copy.
"""

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
HIGH_VALUE_RESOURCES = {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL", "PASS_TOKEN", "OFFICIAL_PERMIT"}
HIGH_VALUE_WINDOW_TYPES = {"GATE", "TASK", "PASS", "PROCESS", "FIXED_PROCESS", "VERIFY_GATE", "RESOURCE"}

# Conservative fallback route. Used only when the graph planner cannot produce
# a path from the current packet. It prevents the bot from idling at S01/S02 due
# to malformed or missing edge data.
MAIN_ROUTE = ("S01", "S02", "S03", "S07", "S09", "S10", "S11", "S12", "S13", "S14", "S15")
ALT_ROUTE = ("S01", "S02", "S03", "S04", "S05", "S11", "S12", "S13", "S14", "S15")


class FreshnessFirstStrategy:
    def __init__(self, player_id: str, config: StrategyConfig, logger: DecisionLogger) -> None:
        self.player_id = player_id
        self.config = config
        self.logger = logger
        self.route_planner = RoutePlanner()
        self._completed_fixed_process_nodes: set[str] = set()
        self._rejected_task_ids: set[str] = set()
        self._rejected_resource_keys: set[tuple[str, str]] = set()
        self._scout_dispatched: set[str] = set()
        self._last_station: str | None = None

    def on_start(self, start_data: dict[str, Any]) -> None:
        self.logger.info("strategy_start", nodes=len(start_data.get("nodes", []) or []), edges=len(start_data.get("edges", []) or []))
        self.logger.info(
            "strategy_variant",
            variant="CLAUDE_SELF_CONTAINED_PURPOSEFUL",
            base="self_contained",
            targetTaskScore=self.config.target_task_score,
            competitiveTaskScore=self.config.competitive_task_score,
            greedTaskScore=self.config.greed_task_score,
        )

    def decide(self, state: GameState) -> ActionBundle:
        self._learn_from_feedback(state)
        if state.me.station != self._last_station and state.me.station is not None:
            self._completed_fixed_process_nodes.discard(state.me.station)
        self._last_station = state.me.station
        try:
            bundle, reason = self._decide(state)
        except Exception as exc:
            self.logger.info("message_error", round=state.frame, error=repr(exc))
            bundle, reason = self._emergency_move(state), "exception_emergency_move"
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
            goodFruit=state.me.good_fruit,
            resources=state.me.resources,
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
            horse = self._horse_action(state, reason="moving_speed")
            if horse is not None:
                return attach(horse), "use_horse_while_moving"
            return attach(wait("moving", active=False)), "system_moving"

        if me.status in BUSY_STATES or me.current_process is not None:
            return attach(wait("busy", active=False)), "system_busy"

        ice = self._ice_box_action(state)
        if ice is not None:
            return attach(ice), "use_ice_box"

        intel = self._intel_action(state)
        if intel is not None:
            return attach(intel), "use_intel"

        if me.station == state.terminal_node:
            if me.verified and me.good_fruit > 0 and me.freshness > 0:
                return attach(ActionBundle(main=MainAction(MainActionType.DELIVER))), "deliver"
            return attach(self._move_to(state, state.gate_node)), "terminal_not_ready_back_to_gate"

        if me.station == state.gate_node:
            if not me.verified:
                if state.phase in RUSH_PHASES:
                    tactic = "BREAK_ORDER" if me.rush_tactic_used_count == 0 else None
                    return attach(ActionBundle(main=MainAction(MainActionType.VERIFY_GATE, target=state.gate_node, rush_tactic=tactic))), "verify_gate"
                # At the gate before rush, active WAIT is acceptable: the gate is not open yet.
                return attach(wait("wait_gate_rush", active=True)), "wait_gate_rush"
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

        resource = self._best_station_resource(state)
        if resource is not None:
            return attach(ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=resource.station, resource_type=resource.resource_type), squad=self._scout_action(state))), f"claim_resource:{resource.resource_type}"

        # Before 90, prefer useful tasks; otherwise claim near high-value resources.
        task2 = self._best_reachable_task(state)
        if task2 is not None:
            return attach(self._move_to(state, task2.target, squad=self._scout_action(state))), f"move_to_task:{task2.template}:{task2.id}"

        resource2 = self._best_reachable_resource(state)
        if resource2 is not None:
            return attach(self._move_to(state, resource2.station, squad=self._scout_action(state))), f"move_to_resource:{resource2.resource_type}"

        return attach(self._move_towards_delivery(state, squad=self._scout_action(state))), "move_towards_delivery"

    def _learn_from_feedback(self, state: GameState) -> None:
        for result in state.action_results:
            if not isinstance(result, dict):
                continue
            accepted = result.get("accepted", result.get("success", True))
            code = str(result.get("code") or result.get("errorCode") or result.get("reason") or "")
            action = str(result.get("action") or result.get("actionType") or "")
            node = str(result.get("targetNodeId") or result.get("nodeId") or state.me.station or "")
            if accepted is False:
                if action == "CLAIM_TASK" or result.get("taskId"):
                    self._rejected_task_ids.add(str(result.get("taskId")))
                if action == "CLAIM_RESOURCE" or result.get("resourceType"):
                    self._rejected_resource_keys.add((node, str(result.get("resourceType"))))
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
        card, reason = self._choose_window_card(state, window)
        self.logger.info("strategy_step", round=state.frame, step="window_card", contestId=window.id, contestType=window.window_type, target=window.target, resourceType=window.resource_type, taskId=window.task_id, roundIndex=window.round_index, chosenCard=card.value, windowStyle="targeted", choiceReason=reason)
        return WindowAction(window.id, card)

    def _choose_window_card(self, state: GameState, window: WindowState) -> tuple[WindowCard, str]:
        high = self._is_high_value_window(state, window)
        me = state.me
        if not high:
            return WindowCard.ABSTAIN, "low_value"
        if me.guard_points > 0:
            return WindowCard.BING_ZHENG, "high_value_guard"
        if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
            return WindowCard.YAN_DIE, "high_value_pass"
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
            return WindowCard.QIANG_XING, "high_value_speed"
        if me.freshness >= 76 and me.good_fruit >= 70:
            return WindowCard.XIAN_GONG, "high_value_fruit"
        return WindowCard.ABSTAIN, "resource_or_fruit_not_enough"

    def _is_high_value_window(self, state: GameState, window: WindowState) -> bool:
        return (
            window.target in {state.gate_node, state.terminal_node}
            or window.window_type in HIGH_VALUE_WINDOW_TYPES
            or bool(window.task_id)
            or (window.resource_type or "") in HIGH_VALUE_RESOURCES
        )

    def _ice_box_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("ICE_BOX"):
            return None
        if me.freshness <= self.config.critical_freshness_threshold:
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="critical_freshness", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.task_score_base >= 60 and me.freshness <= 88:
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="midgame_quality_guard", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.task_score_base >= self.config.target_task_score and me.freshness <= 92:
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX", reason="delivery_quality_guard", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        return None

    def _horse_action(self, state: GameState, *, reason: str) -> ActionBundle | None:
        me = state.me
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        if me.has_resource("FAST_HORSE"):
            self.logger.info("resource_use", round=state.frame, resourceType="FAST_HORSE", reason=reason)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if me.has_resource("SHORT_HORSE"):
            self.logger.info("resource_use", round=state.frame, resourceType="SHORT_HORSE", reason=reason)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _pre_move_resource_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.station is None or me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        target = state.terminal_node if me.verified else state.gate_node
        remaining = self.route_planner.estimate_frames(state, me.station, target)
        if remaining >= 4:
            return self._horse_action(state, reason="pre_move_route")
        return None

    def _intel_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("INTEL") or me.station is None:
            return None
        target = None
        if me.task_score_base >= self.config.target_task_score and not me.verified:
            target = state.gate_node
        if target is None:
            task = self._best_reachable_task(state)
            if task is not None:
                target = task.target
        if target is None:
            resource = self._best_reachable_resource(state)
            if resource is not None:
                target = resource.station
        if target and target != me.station:
            self.logger.info("resource_use", round=state.frame, resourceType="INTEL", reason="scout_target", target=target)
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
        if me.station is None:
            return False
        target = state.terminal_node if me.verified else state.gate_node
        cost = self.route_planner.estimate_frames(state, me.station, target)
        if cost >= 10**8:
            cost = self._route_steps_fallback(me.station, target) * 6
        return state.turns_left <= cost + self.config.endgame_buffer_frames

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        tasks = [t for t in state.station_tasks(state.me.station) if t.id not in self._rejected_task_ids]
        if not tasks:
            return None
        if state.me.task_score_base >= self.config.target_task_score and (self._should_lock_delivery(state) or state.me.freshness < 86 or state.me.good_fruit < 82):
            return None
        return max(tasks, key=lambda t: (140 if state.me.task_score_base < self.config.target_task_score else 0) + t.score * 4 - t.process_frames)

    def _best_reachable_task(self, state: GameState) -> TaskInstance | None:
        me = state.me
        if me.station is None or self._should_lock_delivery(state):
            return None
        direct = self._estimate_frames(state, me.station, state.gate_node)
        best: tuple[int, TaskInstance] | None = None
        for task in state.tasks:
            if task.id in self._rejected_task_ids or not task.available_for(state.player_id) or task.score <= 0:
                continue
            if me.task_score_base >= self.config.target_task_score and (me.freshness < 88 or me.good_fruit < 84):
                continue
            detour = self._estimate_frames(state, me.station, task.target) + task.process_frames + self._estimate_frames(state, task.target, state.gate_node) - direct
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
            stocks = [s for s in stocks if s.resource_type in ROUTE_RESOURCE_TYPES]
        if not stocks:
            return None
        return max(stocks, key=lambda s: self._resource_value(state, s, detour=0))

    def _best_reachable_resource(self, state: GameState) -> ResourceStock | None:
        me = state.me
        if me.station is None:
            return None
        direct = self._estimate_frames(state, me.station, state.gate_node)
        best: tuple[int, ResourceStock] | None = None
        for stock in state.resources:
            if stock.resource_type not in ROUTE_RESOURCE_TYPES or (stock.station, stock.resource_type) in self._rejected_resource_keys:
                continue
            detour = self._estimate_frames(state, me.station, stock.station) + stock.claim_frames + self._estimate_frames(state, stock.station, state.gate_node) - direct
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
            base += 65 if me.freshness <= 90 else 25
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
            resource = self._best_reachable_resource(state)
            if resource is not None:
                target = resource.station
        if target and target not in self._scout_dispatched and target != me.station:
            self.logger.info("squad_eval", round=state.frame, action="SQUAD_SCOUT", target=target, reason="scout_valuable_target")
            return SquadAction(SquadActionType.SQUAD_SCOUT, target)
        return None

    def _move_towards_delivery(self, state: GameState, *, squad: SquadAction | None = None) -> ActionBundle:
        return self._move_to(state, state.terminal_node if state.me.verified else state.gate_node, squad=squad)

    def _move_to(self, state: GameState, target: str | None, *, squad: SquadAction | None = None) -> ActionBundle:
        current = state.me.station
        if target is None or current is None:
            return ActionBundle(squad=squad, debug={"reason": "no_target_or_station"})
        if current == target:
            return ActionBundle(squad=squad, debug={"reason": "already_at_target"})

        plan = self.route_planner.plan(state, current, target)
        next_hop = plan.next_station if plan is not None else None
        if next_hop is None:
            next_hop = self._fallback_next_hop(current, target)
            self.logger.info("route_decision", round=state.frame, station=current, target=target, nextHop=next_hop, reason="fallback_route")
        if next_hop is None:
            # Last resort: do not active WAIT at IDLE. Emit empty with explicit debug; audit will catch it.
            return ActionBundle(squad=squad, debug={"reason": "no_route"})
        return ActionBundle(main=MainAction(MainActionType.MOVE, target=next_hop), squad=squad)

    def _emergency_move(self, state: GameState) -> ActionBundle:
        target = state.terminal_node if state.me.verified else state.gate_node
        return self._move_to(state, target)

    def _estimate_frames(self, state: GameState, start: str | None, target: str) -> int:
        value = self.route_planner.estimate_frames(state, start, target)
        if value < 10**8:
            return value
        return self._route_steps_fallback(start, target) * 6

    def _route_steps_fallback(self, start: str | None, target: str) -> int:
        if start is None:
            return 10**8
        for route in (MAIN_ROUTE, ALT_ROUTE):
            if start in route and target in route:
                return abs(route.index(target) - route.index(start))
        return 10**8

    def _fallback_next_hop(self, start: str, target: str) -> str | None:
        for route in (MAIN_ROUTE, ALT_ROUTE):
            if start not in route or target not in route:
                continue
            i = route.index(start)
            j = route.index(target)
            if i < j:
                return route[i + 1]
            if i > j:
                return route[i - 1]
        return None


BaselineStrategy = FreshnessFirstStrategy
RoadMasterStrategy = FreshnessFirstStrategy
