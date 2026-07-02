from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lizhi_agent.actions import (
    ActionBundle,
    MainAction,
    MainActionType,
    SquadAction,
    SquadActionType,
    WindowAction,
    WindowCard,
    wait,
)
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, ResourceStock, TaskInstance, WindowState
from lizhi_agent.route_planner import RoutePlanner


BUSY_STATES = {
    ConvoyStatus.PROCESSING,
    ConvoyStatus.VERIFYING,
    ConvoyStatus.RESTING,
    ConvoyStatus.FORCED_PASSING,
    ConvoyStatus.CONTESTING,
}

MOVING_STATES = {ConvoyStatus.MOVING, ConvoyStatus.WAITING}
RUSH_PHASES = {"RUSH", "BANQUET", "ENDGAME", "FINAL", "宫宴冲刺"}


@dataclass(frozen=True)
class Decision:
    bundle: ActionBundle
    reason: str


class WindowStrategy:
    """Low-regret fixed policy for 3-card contest windows.

    The card matrix has no universally dominant card.  This baseline spends
    scarce resources only on high-value windows and otherwise prefers cheap
    cards or abstention to avoid fruit/freshness damage.
    """

    def choose_card(self, state: GameState, window: WindowState) -> WindowCard:
        me = state.me
        high_value = window.window_type in {"GATE", "TASK", "PASS"} or window.resource_type in {"FAST_HORSE", "ICE_BOX"}
        if high_value and me.guard_points > 0:
            return WindowCard.BING_ZHENG
        if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
            return WindowCard.YAN_DIE
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
            return WindowCard.QIANG_XING
        if high_value and me.freshness >= 85 and me.good_fruit >= 80:
            return WindowCard.XIAN_GONG
        return WindowCard.ABSTAIN


class BaselineStrategy:
    """Layered baseline strategy.

    The strategy is deliberately conservative: first remain protocol-correct,
    then avoid repeated invalid actions, then secure delivery.  Optional tasks
    and resources are useful only when they do not trap the convoy off-route.
    """

    def __init__(self, player_id: str, config: StrategyConfig, logger: DecisionLogger) -> None:
        self.player_id = player_id
        self.config = config
        self.logger = logger
        self.route_planner = RoutePlanner()
        self.window_strategy = WindowStrategy()
        self._start_seen = False
        self._scout_dispatched: set[str] = set()
        self._last_station: str | None = None
        self._station_since_frame: int | None = None
        self._station_escape_until: dict[str, int] = {}
        self._object_cooldown_until: dict[str, int] = {}
        self._window_seen: dict[str, int] = {}
        self._completed_fixed_process_nodes: set[str] = set()
        self._rejected_fixed_process_nodes: set[str] = set()
        self._rejected_task_ids: set[str] = set()
        self._rejected_resource_keys: set[tuple[str, str]] = set()

    def on_start(self, start_data: dict) -> None:
        self._start_seen = True
        self.logger.info(
            "strategy_start",
            nodes=len(start_data.get("nodes", []) or []),
            edges=len(start_data.get("edges", []) or []),
        )

    def decide(self, state: GameState) -> ActionBundle:
        self._learn_from_feedback(state)
        self._update_station_tracking(state)
        if state.me.station != self._last_station and state.me.station is not None:
            # Fixed processing must be redone when the convoy leaves and later
            # re-enters the same processing station.  Keep the completion mark
            # only while we remain on that station after PROCESS_COMPLETE.
            self._completed_fixed_process_nodes.discard(state.me.station)
        self._last_station = state.me.station

        self._log_state_snapshot(state)
        decision = self._decide(state)
        if decision.bundle.squad is not None and decision.bundle.squad.action == SquadActionType.SQUAD_SCOUT:
            self._scout_dispatched.add(decision.bundle.squad.target)
        self.logger.info(
            "decision",
            round=state.frame,
            phase=state.phase,
            station=state.me.station,
            status=state.me.status.value,
            score=state.me.total_score,
            taskScore=state.me.task_score_base,
            freshness=state.me.freshness,
            reason=decision.reason,
            actions=decision.bundle.to_actions(),
        )
        return decision.bundle

    def _learn_from_feedback(self, state: GameState) -> None:
        for event in state.events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event") or event.get("eventType") or event.get("type") or "").upper()
            node_id = event.get("targetNodeId") or event.get("nodeId")
            task_id = event.get("taskId")
            if event_type in {"PROCESS_COMPLETE", "FIXED_PROCESS_COMPLETE"} and node_id:
                self._completed_fixed_process_nodes.add(str(node_id))
                self.logger.info("feedback_learn", learned="fixed_process_completed", nodeId=node_id, event=event)
            if event_type in {"TASK_COMPLETE", "CLAIM_TASK_COMPLETE"} and task_id:
                self._rejected_task_ids.discard(str(task_id))
            if event_type in {"WINDOW_CONTEST_DRAW", "WINDOW_CONTEST_REPEAT_SUPPRESSED", "CONTEST_DRAW"}:
                object_key = self._event_object_key(event)
                if object_key is not None:
                    self._cooldown_object(state, object_key, f"event:{event_type}")

        for result in state.action_results:
            if not isinstance(result, dict):
                continue
            action = str(result.get("action") or result.get("actionType") or result.get("type") or "").upper()
            accepted = result.get("accepted")
            success = result.get("success")
            code = str(result.get("code") or result.get("errorCode") or result.get("reason") or result.get("message") or "").upper()
            failed = accepted is False or success is False or code in {
                "PROCESS_NOT_AVAILABLE",
                "NOT_AT_TARGET_NODE",
                "TASK_NOT_AVAILABLE",
                "TASK_NOT_FOUND",
                "TASK_ALREADY_COMPLETED",
                "RESOURCE_NOT_ENOUGH",
                "OBJECT_BUSY",
            }
            if not failed:
                continue
            node_id = result.get("targetNodeId") or result.get("nodeId")
            task_id = result.get("taskId")
            resource_type = result.get("resourceType")
            if action in {"PROCESS", "DOCK"} and node_id:
                self._rejected_fixed_process_nodes.add(str(node_id))
                self.logger.info("feedback_learn", learned="fixed_process_rejected", nodeId=node_id, code=code, result=result)
            if action == "CLAIM_TASK" and task_id:
                self._rejected_task_ids.add(str(task_id))
                self._cooldown_object(state, self._task_object_key(str(task_id)), f"reject:{code}")
                self.logger.info("feedback_learn", learned="task_rejected", taskId=task_id, code=code, result=result)
            if action == "CLAIM_RESOURCE" and node_id and resource_type:
                self._rejected_resource_keys.add((str(node_id), str(resource_type)))
                self._cooldown_object(state, self._resource_object_key(str(node_id), str(resource_type)), f"reject:{code}")
                self.logger.info("feedback_learn", learned="resource_rejected", nodeId=node_id, resourceType=resource_type, code=code, result=result)

    def _log_state_snapshot(self, state: GameState) -> None:
        me = state.me
        gate_cost = self.route_planner.estimate_frames(state, me.station, state.gate_node) if me.station else None
        terminal_cost = self.route_planner.estimate_frames(state, state.gate_node, state.terminal_node)
        opponent_gate_cost = None
        if state.opponent is not None and state.opponent.station:
            opponent_gate_cost = self.route_planner.estimate_frames(state, state.opponent.station, state.gate_node)
        self.logger.info(
            "state_snapshot",
            round=state.frame,
            phase=state.phase,
            status=me.status.value,
            stateClass=self._state_class(me.status),
            station=me.station,
            target=me.target,
            verified=me.verified,
            delivered=me.delivered,
            goodFruit=me.good_fruit,
            badFruit=me.bad_fruit,
            freshness=me.freshness,
            taskScore=me.task_score_base,
            bountyScore=me.bounty_score,
            totalScore=me.total_score,
            resources=me.resources,
            buffs=me.buffs,
            squadAvailable=me.squad_available,
            guardPoints=me.guard_points,
            tasks=len(state.tasks),
            resourcesOnMap=len(state.resources),
            windows=len(state.windows),
            events=len(state.events),
            gateCost=gate_cost,
            opponentGateCost=opponent_gate_cost,
            terminalCost=terminal_cost,
            turnsLeft=state.turns_left,
            rejectedTasks=list(sorted(self._rejected_task_ids))[:5],
            rejectedProcessNodes=list(sorted(self._rejected_fixed_process_nodes))[:5],
            stationStay=self._station_stay_frames(state),
            stationEscapeUntil=self._station_escape_until.get(me.station or ""),
        )

    def _state_class(self, status: ConvoyStatus) -> str:
        if status in MOVING_STATES:
            return "MOVING_GUARD"
        if status in BUSY_STATES:
            return "BUSY_GUARD"
        if status in {ConvoyStatus.DELIVERED, ConvoyStatus.RETIRED}:
            return "TERMINAL_GUARD"
        return "PLANNING"

    def _decide(self, state: GameState) -> Decision:
        me = state.me

        window = state.active_window()
        if window is not None:
            object_key = self._window_object_key(window)
            if object_key is not None:
                self._window_seen[object_key] = self._window_seen.get(object_key, 0) + 1
            if object_key is not None and (
                self._is_object_on_cooldown(state, object_key)
                or self._is_station_escape_active(state, window.target or state.me.station)
                or self._window_seen.get(object_key, 0) > self.config.max_window_rounds_before_abstain
            ):
                self._cooldown_object(state, object_key, "window_stall")
                self.logger.info(
                    "stall_breaker",
                    kind="window",
                    station=window.target or state.me.station,
                    objectKey=object_key,
                    action="ABSTAIN",
                    reason="争抢窗口重复出现，停止加码，保留荔枝继续主线",
                )
                return Decision(ActionBundle(window=WindowAction(window.id, WindowCard.ABSTAIN)), f"window_stall:{object_key}")
            card = self.window_strategy.choose_card(state, window)
            self.logger.info(
                "strategy_step",
                step="window_card",
                contestId=window.id,
                contestType=window.window_type,
                target=window.target,
                resourceType=window.resource_type,
                taskId=window.task_id,
                roundIndex=window.round_index,
                chosenCard=card.value,
            )
            return Decision(ActionBundle(window=WindowAction(window.id, card)), f"window:{window.window_type}:{card.value}")

        if me.delivered or me.status == ConvoyStatus.DELIVERED:
            return Decision(wait("already_delivered", active=False), "already_delivered")

        if me.retired or me.status == ConvoyStatus.RETIRED:
            return Decision(wait("retired", active=False), "retired")

        if me.status in MOVING_STATES:
            horse = self._moving_horse_action(state)
            if horse is not None:
                return Decision(horse, "use_horse_while_moving")
            return Decision(wait(f"moving:{me.status.value}", active=False), f"moving:{me.status.value}")

        if me.status in BUSY_STATES:
            return Decision(wait(f"busy:{me.status.value}", active=False), f"busy:{me.status.value}")

        fresh_action = self._freshness_action(state)
        if fresh_action is not None:
            return Decision(fresh_action, "use_ice_box")

        if me.station == state.terminal_node:
            if me.verified and me.good_fruit > 0 and me.freshness > 0:
                return Decision(ActionBundle(main=MainAction(MainActionType.DELIVER)), "deliver")
            return Decision(self._move_to(state, state.gate_node), "leave_terminal_not_ready")

        if me.station == state.gate_node:
            if not me.verified:
                if self._can_verify_gate(state):
                    return Decision(self._verify_action(state), "verify_gate")
                return Decision(wait("at_gate_before_rush", active=False), "at_gate_before_rush")
            return Decision(self._move_to(state, state.terminal_node), "gate_to_terminal")

        if self._need_endgame(state) or self._opponent_pressure(state):
            self.logger.info("strategy_step", step="delivery_guard", reason="rush_deadline_or_opponent_pressure")
            return Decision(self._move_towards_delivery(state), "delivery_guard")

        fixed_process = self._fixed_process_action(state)
        if fixed_process is not None:
            return Decision(fixed_process, "fixed_process")

        if self._is_station_escape_active(state):
            self.logger.info(
                "stall_breaker",
                kind="station",
                station=me.station,
                stayFrames=self._station_stay_frames(state),
                escapeUntil=self._station_escape_until.get(me.station or ""),
                action="MOVE_MAINLINE",
                reason="当前站点争抢/休整过久，暂停本地任务和资源，直奔主线",
            )
            return Decision(self._move_towards_delivery(state), "station_stall_escape")

        station_task = self._best_station_task(state)
        if station_task is not None:
            return Decision(self._claim_task(station_task), f"claim_task:{station_task.template}:{station_task.id}")

        station_resource = self._best_station_resource(state)
        if station_resource is not None:
            return Decision(self._claim_resource(station_resource), f"claim_resource:{station_resource.resource_type}")

        scout = self._squad_scout_action(state)
        route_task = self._best_reachable_task(state)
        if route_task is not None:
            return Decision(self._move_towards_node(state, route_task.target, squad=scout), f"move_to_task:{route_task.template}:{route_task.id}")

        route_resource = self._best_reachable_resource(state)
        if route_resource is not None:
            return Decision(self._move_towards_node(state, route_resource.station, squad=scout), f"move_to_resource:{route_resource.resource_type}")

        return Decision(self._move_towards_delivery(state, squad=scout), "move_towards_delivery")

    def _need_endgame(self, state: GameState) -> bool:
        me = state.me
        if state.phase in RUSH_PHASES:
            return True
        if me.station is None:
            return False
        gate_cost = self.route_planner.estimate_frames(state, me.station, state.gate_node)
        terminal_cost = self.route_planner.estimate_frames(state, state.gate_node, state.terminal_node)
        if gate_cost >= 10**8 or terminal_cost >= 10**8:
            return False
        verify_cost = 0 if me.verified else 6
        return state.turns_left <= gate_cost + terminal_cost + verify_cost + self.config.endgame_buffer_frames

    def _opponent_pressure(self, state: GameState) -> bool:
        if state.opponent is None or state.me.station is None or state.opponent.station is None:
            return False
        if state.me.task_score_base < self.config.target_task_score:
            return False
        if state.opponent.verified or state.opponent.delivered:
            return True
        my_gate = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        opp_gate = self.route_planner.estimate_frames(state, state.opponent.station, state.gate_node)
        pressure = opp_gate + 35 < my_gate
        if pressure:
            self.logger.info("opponent_pressure", myGateCost=my_gate, opponentGateCost=opp_gate)
        return pressure

    def _can_verify_gate(self, state: GameState) -> bool:
        return state.phase in RUSH_PHASES

    def _verify_action(self, state: GameState) -> ActionBundle:
        # BREAK_ORDER is strongest when it shortens gate verification by 3
        # frames.  Use it only in true endgame and only once.
        rush = "BREAK_ORDER" if state.me.rush_tactic_used_count == 0 and state.phase in RUSH_PHASES else None
        return ActionBundle(main=MainAction(MainActionType.VERIFY_GATE, target=state.gate_node, rush_tactic=rush))

    def _freshness_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.freshness <= self.config.critical_freshness_threshold and me.has_resource("ICE_BOX"):
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        return None

    def _moving_horse_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        if me.has_resource("FAST_HORSE"):
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if me.has_resource("SHORT_HORSE") and state.turns_left < 160:
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _fixed_process_action(self, state: GameState) -> ActionBundle | None:
        station = state.station(state.me.station)
        if station is None or not station.process_type or station.process_round <= 0:
            return None
        if state.me.current_process is not None:
            return None
        if station.process_type == "VERIFY":
            return None
        if station.id in self._completed_fixed_process_nodes:
            self.logger.info("fixed_process_skip", station=station.id, processType=station.process_type, reason="already_completed_this_visit")
            return None
        if station.id in self._rejected_fixed_process_nodes:
            self.logger.info("fixed_process_skip", station=station.id, processType=station.process_type, reason="recently_rejected")
            return None
        # The task book says fixed processing stations, including BOARD at S04,
        # submit PROCESS.  DOCK is kept in the action enum, but using PROCESS is
        # the safer baseline for the current judge implementation.
        self.logger.info("fixed_process_eval", station=station.id, processType=station.process_type, action="PROCESS")
        return ActionBundle(main=MainAction(MainActionType.PROCESS, target=station.id))

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        tasks = [
            task
            for task in state.station_tasks(state.me.station)
            if task.id not in self._rejected_task_ids
            and not self._is_object_on_cooldown(state, self._task_object_key(task.id))
        ]
        if not tasks:
            self.logger.info("task_eval_station", station=state.me.station, candidates=[])
            return None
        if state.me.task_score_base >= self.config.greed_task_score and not self._need_endgame(state):
            return None

        def score(task: TaskInstance) -> tuple[int, int]:
            threshold_bonus = 100 if state.me.task_score_base < self.config.target_task_score and task.score >= 30 else 0
            clear_bonus = 20 if task.template == "T04" else 0
            return threshold_bonus + clear_bonus + task.score, -task.process_frames

        best = max(tasks, key=score)
        self.logger.info(
            "task_eval_station",
            station=state.me.station,
            candidates=[
                {
                    "taskId": task.id,
                    "template": task.template,
                    "score": task.score,
                    "processFrames": task.process_frames,
                    "rank": score(task),
                }
                for task in tasks
            ],
            chosen=best.id,
        )
        if state.me.task_score_base < self.config.target_task_score:
            return best
        if best.score >= 30 and not self._need_endgame(state):
            return best
        return None

    def _best_station_resource(self, state: GameState) -> ResourceStock | None:
        stocks = [
            stock
            for stock in state.station_resources(state.me.station)
            if (stock.station, stock.resource_type) not in self._rejected_resource_keys
            and not self._is_object_on_cooldown(state, self._resource_object_key(stock.station, stock.resource_type))
        ]
        if not stocks:
            self.logger.info("resource_eval_station", station=state.me.station, candidates=[])
            return None
        priority = {name: i for i, name in enumerate(self.config.resource_priority)}
        useful = [stock for stock in stocks if stock.resource_type in priority]
        if self._need_endgame(state) or self._opponent_pressure(state):
            useful = [stock for stock in useful if stock.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE"}]
        if not useful:
            self.logger.info(
                "resource_eval_station",
                station=state.me.station,
                candidates=[{"resourceType": stock.resource_type, "amount": stock.amount} for stock in stocks],
                chosen=None,
            )
            return None
        chosen = min(useful, key=lambda stock: priority.get(stock.resource_type, 999))
        self.logger.info(
            "resource_eval_station",
            station=state.me.station,
            candidates=[
                {
                    "resourceType": stock.resource_type,
                    "amount": stock.amount,
                    "priority": priority.get(stock.resource_type, 999),
                }
                for stock in stocks
            ],
            chosen=chosen.resource_type,
        )
        return chosen

    def _best_reachable_task(self, state: GameState) -> TaskInstance | None:
        if state.me.task_score_base >= self.config.target_task_score:
            return None
        if state.me.station is None:
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        candidates: list[tuple[int, TaskInstance, int, int, int]] = []
        for task in state.tasks:
            if task.id in self._rejected_task_ids:
                continue
            if self._is_object_on_cooldown(state, self._task_object_key(task.id)):
                continue
            if not task.available_for(state.player_id) or task.score <= 0:
                continue
            to_task = self.route_planner.estimate_frames(state, state.me.station, task.target)
            to_gate = self.route_planner.estimate_frames(state, task.target, state.gate_node)
            detour = to_task + task.process_frames + to_gate - direct
            max_detour = self.config.max_task_detour_frames
            if task.score >= 30:
                max_detour += 12
            if detour <= max_detour:
                value = task.score * 4 - max(0, detour)
                if task.score >= 30:
                    value += 40
                candidates.append((value, task, detour, to_task, to_gate))
        if not candidates:
            self.logger.info("task_eval_reachable", directToGate=direct, candidates=[])
            return None
        chosen_value, chosen, chosen_detour, chosen_to_task, chosen_to_gate = max(candidates, key=lambda item: item[0])
        self.logger.info(
            "task_eval_reachable",
            directToGate=direct,
            candidates=[
                {
                    "taskId": task.id,
                    "template": task.template,
                    "target": task.target,
                    "score": task.score,
                    "value": value,
                    "detour": detour,
                    "toTask": to_task,
                    "toGate": to_gate,
                }
                for value, task, detour, to_task, to_gate in sorted(candidates, key=lambda item: item[0], reverse=True)[:5]
            ],
            chosen=chosen.id,
            chosenValue=chosen_value,
            chosenDetour=chosen_detour,
            chosenToTask=chosen_to_task,
            chosenToGate=chosen_to_gate,
        )
        return chosen

    def _best_reachable_resource(self, state: GameState) -> ResourceStock | None:
        if state.me.station is None:
            return None
        if self._opponent_pressure(state):
            self.logger.info("resource_eval_reachable", candidates=[], reason="opponent_pressure")
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        priority = {name: i for i, name in enumerate(self.config.resource_priority)}
        candidates: list[tuple[int, ResourceStock, int]] = []
        for stock in state.resources:
            if (stock.station, stock.resource_type) in self._rejected_resource_keys:
                continue
            if self._is_object_on_cooldown(state, self._resource_object_key(stock.station, stock.resource_type)):
                continue
            if stock.resource_type not in priority:
                continue
            to_res = self.route_planner.estimate_frames(state, state.me.station, stock.station)
            to_gate = self.route_planner.estimate_frames(state, stock.station, state.gate_node)
            detour = to_res + stock.claim_frames + to_gate - direct
            if detour <= self.config.max_resource_detour_frames:
                candidates.append((100 - priority[stock.resource_type] * 10 - max(0, detour), stock, detour))
        if not candidates:
            self.logger.info("resource_eval_reachable", directToGate=direct, candidates=[])
            return None
        chosen_value, chosen, chosen_detour = max(candidates, key=lambda item: item[0])
        self.logger.info(
            "resource_eval_reachable",
            directToGate=direct,
            candidates=[
                {
                    "resourceType": stock.resource_type,
                    "station": stock.station,
                    "value": value,
                    "detour": detour,
                }
                for value, stock, detour in sorted(candidates, key=lambda item: item[0], reverse=True)[:5]
            ],
            chosen=chosen.resource_type,
            chosenStation=chosen.station,
            chosenValue=chosen_value,
            chosenDetour=chosen_detour,
        )
        return chosen

    def _squad_scout_action(self, state: GameState) -> SquadAction | None:
        if state.phase in RUSH_PHASES or state.me.squad_available <= 0:
            return None
        if state.me.station is None:
            return None
        forbidden = {state.me.station, state.start_node, state.gate_node, state.terminal_node, *map(str, state.roles.get("safeZoneNodeIds", []) or [])}
        for target in self.config.scout_targets:
            if target in forbidden:
                self.logger.info("squad_eval_skip", target=target, reason="forbidden_scout_target")
                continue
            if target in self._scout_dispatched or self._has_own_scout_marker(state, target):
                continue
            if self.route_planner.estimate_frames(state, state.me.station, target) < 10**8:
                self.logger.info("squad_eval", action="SQUAD_SCOUT", target=target, reason="preferred_scout_target")
                return SquadAction(SquadActionType.SQUAD_SCOUT, target)
        self.logger.info("squad_eval", action=None, reason="no_available_scout_target")
        return None

    def _has_own_scout_marker(self, state: GameState, target: str) -> bool:
        station = state.station(target)
        if station is None:
            return False
        markers = station.raw.get("scouted")
        if not isinstance(markers, list):
            return False
        for marker in markers:
            if isinstance(marker, dict) and marker.get("teamId") == state.me.team_id and marker.get("remainingTriggers", 1):
                return True
        return False

    def _claim_task(self, task: TaskInstance) -> ActionBundle:
        # Protocol examples require taskId for CLAIM_TASK.  Do not include
        # targetNodeId here; some judge versions reject the extra field.
        return ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, task_id=task.id))

    def _claim_resource(self, resource: ResourceStock) -> ActionBundle:
        return ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=resource.station, resource_type=resource.resource_type))

    def _update_station_tracking(self, state: GameState) -> None:
        station = state.me.station
        if station is None:
            self._station_since_frame = None
            return
        if station != self._last_station or self._station_since_frame is None:
            self._station_since_frame = state.frame
            return
        if self._is_mainline_station(state, station):
            return
        stay_frames = self._station_stay_frames(state)
        if stay_frames < self.config.station_stall_frames:
            return
        if self._is_station_escape_active(state, station):
            return
        until = state.frame + self.config.station_escape_frames
        self._station_escape_until[station] = until
        self.logger.info(
            "stall_breaker",
            kind="station",
            station=station,
            stayFrames=stay_frames,
            escapeUntil=until,
            action="ARM_ESCAPE",
            reason="同一站点停留过久，疑似任务/资源争抢循环",
        )

    def _station_stay_frames(self, state: GameState) -> int:
        if self._station_since_frame is None:
            return 0
        return max(0, state.frame - self._station_since_frame)

    def _is_station_escape_active(self, state: GameState, station: str | None = None) -> bool:
        station_id = station or state.me.station
        if station_id is None:
            return False
        until = self._station_escape_until.get(station_id)
        if until is None:
            return False
        if state.frame <= until:
            return True
        self._station_escape_until.pop(station_id, None)
        return False

    def _is_mainline_station(self, state: GameState, station: str) -> bool:
        return station in {state.start_node, state.gate_node, state.terminal_node}

    def _cooldown_object(self, state: GameState, object_key: str, reason: str) -> None:
        until = state.frame + self.config.object_cooldown_frames
        if self._object_cooldown_until.get(object_key, 0) >= until:
            return
        self._object_cooldown_until[object_key] = until
        self.logger.info(
            "stall_breaker",
            kind="object",
            objectKey=object_key,
            cooldownUntil=until,
            reason=reason,
        )

    def _is_object_on_cooldown(self, state: GameState, object_key: str) -> bool:
        until = self._object_cooldown_until.get(object_key)
        if until is None:
            return False
        if state.frame <= until:
            return True
        self._object_cooldown_until.pop(object_key, None)
        return False

    def _task_object_key(self, task_id: str) -> str:
        return f"TASK:{task_id}"

    def _resource_object_key(self, station: str, resource_type: str) -> str:
        return f"RESOURCE:{station}:{resource_type}"

    def _window_object_key(self, window: WindowState) -> str | None:
        raw_key = window.raw.get("objectKey")
        if raw_key:
            return str(raw_key)
        if window.task_id:
            return self._task_object_key(window.task_id)
        if window.target and window.resource_type:
            return self._resource_object_key(str(window.target), str(window.resource_type))
        if window.id:
            return f"WINDOW:{window.id}"
        return None

    def _event_object_key(self, event: dict[str, Any]) -> str | None:
        raw_key = event.get("objectKey")
        if raw_key:
            return str(raw_key)
        task_id = event.get("taskId")
        if task_id:
            return self._task_object_key(str(task_id))
        node_id = event.get("targetNodeId") or event.get("nodeId")
        resource_type = event.get("resourceType")
        if node_id and resource_type:
            return self._resource_object_key(str(node_id), str(resource_type))
        contest_id = event.get("contestId")
        if contest_id:
            return f"WINDOW:{contest_id}"
        return None

    def _move_towards_delivery(self, state: GameState, squad: SquadAction | None = None) -> ActionBundle:
        target = state.terminal_node if state.me.verified else state.gate_node
        return self._move_towards_node(state, target, squad=squad)

    def _move_towards_node(self, state: GameState, target: str, squad: SquadAction | None = None) -> ActionBundle:
        if state.me.station is None:
            return wait("unknown_station", active=False)
        next_hop = self.route_planner.next_hop_to_any(state, state.me.station, (target,))
        if next_hop is None:
            self.logger.info("route_decision", fromNode=state.me.station, target=target, nextHop=None, reason="no_route")
            return wait("no_route", active=False)
        self.logger.info("route_decision", fromNode=state.me.station, target=target, nextHop=next_hop)
        return self._move_to(state, next_hop, squad=squad)

    def _move_to(self, state: GameState, target: str, squad: SquadAction | None = None) -> ActionBundle:
        station = state.station(target)
        if station is not None and station.has_obstacle:
            t04 = self._t04_for_target(state, target)
            if t04 is not None:
                self.logger.info("blocker_decision", target=target, blocker="obstacle", action="CLAIM_TASK", taskId=t04.id)
                return self._claim_task(t04)
            if state.me.good_fruit > 5:
                self.logger.info("blocker_decision", target=target, blocker="obstacle", action="CLEAR")
                return ActionBundle(main=MainAction(MainActionType.CLEAR, target=target), squad=squad)
            self.logger.info("blocker_decision", target=target, blocker="obstacle", action="FORCED_PASS")
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=squad)
        if station is not None and station.has_enemy_guard(state.me.team_id):
            if state.me.bad_fruit >= 2 or state.me.good_fruit >= 95:
                self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="BREAK_GUARD")
                return ActionBundle(
                    main=MainAction(
                        MainActionType.BREAK_GUARD,
                        target=target,
                        good_fruit=0 if state.me.bad_fruit >= 2 else 1,
                        bad_fruit=min(2, state.me.bad_fruit),
                    ),
                    squad=squad,
                )
            self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="FORCED_PASS")
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=squad)
        self.logger.info("move_decision", target=target, action="MOVE")
        return ActionBundle(main=MainAction(MainActionType.MOVE, target=target), squad=squad)

    def _t04_for_target(self, state: GameState, target: str) -> TaskInstance | None:
        for task in state.tasks:
            if task.template == "T04" and task.target == target and task.available_for(state.player_id) and task.id not in self._rejected_task_ids:
                return task
        return None
