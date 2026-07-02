from __future__ import annotations

import hashlib
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
MOVING_STATES = {ConvoyStatus.MOVING}
RUSH_PHASES = {"RUSH", "BANQUET", "ENDGAME", "FINAL", "宫宴冲刺"}
PLANNING_STATES = {ConvoyStatus.IDLE, ConvoyStatus.WAITING, ConvoyStatus.UNKNOWN, ConvoyStatus.COST_BANKRUPT}
PROCESS_RETRY_CODES = {"PROCESS_REQUIRED", "PROCESS_INTERRUPTED", "INTERRUPTED"}
PROCESS_HARD_REJECT_CODES = {"PROCESS_NOT_AVAILABLE", "NOT_AT_TARGET_NODE", "INVALID_TARGET"}
WINDOW_REJECT_CODES = {
    "WINDOW_NOT_ACTIVE",
    "WINDOW_NOT_AVAILABLE",
    "WINDOW_NOT_YOUR_TURN",
    "WINDOW_CARD_INVALID",
    "WINDOW_DRAW_RETRY_LIMIT",
    "CONTEST_NOT_ACTIVE",
    "CONTEST_NOT_FOUND",
    "INVALID_CONTEST",
    "INVALID_ACTION",
}
WINDOW_TERMINAL_STATUSES = {"SUPPRESSED", "RESOLVED", "FINISHED", "FINISH", "ENDED", "END", "CLOSED", "COMPLETED", "COMPLETE", "SETTLED"}
WINDOW_HARD_MAX_SENDS = 3
SCOUT_PATH_LOOKAHEAD = 3


@dataclass(frozen=True)
class Decision:
    bundle: ActionBundle
    reason: str


@dataclass(frozen=True)
class WindowChoice:
    card: WindowCard
    style: str
    reason: str
    roll: int | None = None


class WindowStrategy:
    """Low-regret mixed policy for contest windows."""

    def choose(self, state: GameState, window: WindowState, config: StrategyConfig) -> WindowChoice:
        me = state.me
        high_value = window.window_type in {"GATE", "TASK", "PASS"} or window.resource_type in {"FAST_HORSE", "ICE_BOX"}
        opponent_card = self._opponent_revealed_card(state, window)
        if opponent_card is not None:
            counter = self._counter_card(state, opponent_card, high_value)
            if counter is not None:
                return WindowChoice(counter, "COUNTER_LAST_CARD", f"counter previous opponent card {opponent_card.value}")
        if self._is_opening_fight(state, window, config):
            options = self._opening_options(state, high_value)
            card, roll = self._weighted_pick(state, window, options)
            return WindowChoice(card, "OPENING_MIX", f"开局窗口混合策略，候选={self._options_text(options)}", roll)
        if high_value and me.guard_points > 0:
            return WindowChoice(WindowCard.BING_ZHENG, "FIXED_VALUE", "高价值窗口且有护卫点")
        if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
            return WindowChoice(WindowCard.YAN_DIE, "FIXED_RESOURCE", "有通行类资源")
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
            return WindowChoice(WindowCard.QIANG_XING, "FIXED_SPEED", "有速度资源/增益")
        if high_value and me.freshness >= 85 and me.good_fruit >= 80:
            return WindowChoice(WindowCard.XIAN_GONG, "FIXED_FRUIT", "高价值窗口且果况健康")
        return WindowChoice(WindowCard.ABSTAIN, "SAVE_FRUIT", "价值不够或资源不足")

    def choose_card(self, state: GameState, window: WindowState) -> WindowCard:
        return self.choose(state, window, StrategyConfig.default()).card

    def _is_opening_fight(self, state: GameState, window: WindowState, config: StrategyConfig) -> bool:
        if state.frame > config.opening_window_mix_frames:
            return False
        target = window.target or state.me.station
        if target in {None, state.start_node, state.gate_node, state.terminal_node}:
            return False
        return window.window_type in {"TASK", "RESOURCE", "PASS", "UNKNOWN"} or window.resource_type is not None

    def _opening_options(self, state: GameState, high_value: bool) -> list[tuple[WindowCard, int]]:
        me = state.me
        options: list[tuple[WindowCard, int]] = []
        if me.guard_points > 0:
            options.append((WindowCard.BING_ZHENG, 42 if high_value else 30))
        if me.freshness >= 86 and me.good_fruit >= 82:
            options.append((WindowCard.XIAN_GONG, 34 if high_value else 24))
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
            options.append((WindowCard.QIANG_XING, 28))
        if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
            options.append((WindowCard.YAN_DIE, 26))
        if not high_value or me.freshness < 75 or me.good_fruit < 70:
            options.append((WindowCard.ABSTAIN, 18))
        if not options:
            options.append((WindowCard.ABSTAIN, 100))
        return options

    def _weighted_pick(self, state: GameState, window: WindowState, options: list[tuple[WindowCard, int]]) -> tuple[WindowCard, int]:
        total = sum(weight for _, weight in options)
        seed = "|".join([
            state.player_id,
            str(window.id),
            str(window.target or state.me.station),
            str(window.task_id or ""),
            str(window.resource_type or ""),
            str(window.round_index),
            str(state.frame // 3),
        ])
        roll = int.from_bytes(hashlib.blake2s(seed.encode("utf-8"), digest_size=4).digest(), "big") % total
        cursor = 0
        for card, weight in options:
            cursor += weight
            if roll < cursor:
                return card, roll
        return options[-1][0], roll

    def _options_text(self, options: list[tuple[WindowCard, int]]) -> str:
        return ",".join(f"{card.value}:{weight}" for card, weight in options)

    def _opponent_revealed_card(self, state: GameState, window: WindowState) -> WindowCard | None:
        cards = window.raw.get("cards")
        if not isinstance(cards, dict) or not cards:
            return None
        my_team = str(state.me.team_id or "")
        for owner, card_value in cards.items():
            if my_team and str(owner) == my_team:
                continue
            try:
                return WindowCard(str(card_value))
            except ValueError:
                continue
        return None

    def _counter_card(self, state: GameState, opponent_card: WindowCard, high_value: bool) -> WindowCard | None:
        me = state.me
        if opponent_card == WindowCard.YAN_DIE:
            if me.guard_points > 0:
                return WindowCard.BING_ZHENG
            if high_value and me.freshness >= 82 and me.good_fruit >= 75:
                return WindowCard.XIAN_GONG
        if opponent_card == WindowCard.QIANG_XING:
            if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
                return WindowCard.YAN_DIE
            if me.guard_points > 0:
                return WindowCard.BING_ZHENG
        if opponent_card == WindowCard.XIAN_GONG:
            if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
                return WindowCard.QIANG_XING
        if opponent_card == WindowCard.BING_ZHENG:
            if high_value and me.freshness >= 85 and me.good_fruit >= 85:
                return WindowCard.XIAN_GONG
        return None


class BaselineStrategy:
    """Conservative baseline: legal first, delivery second, greed last."""

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
        self._suppressed_window_keys: set[str] = set()
        self._completed_fixed_process_nodes: set[str] = set()
        self._rejected_fixed_process_nodes: set[str] = set()
        self._forced_process_nodes: set[str] = set()
        self._pending_process_until: dict[str, int] = {}
        self._pending_process_started_at: dict[str, int] = {}
        self._rejected_task_ids: set[str] = set()
        self._rejected_resource_keys: set[tuple[str, str]] = set()

    def on_start(self, start_data: dict) -> None:
        self._start_seen = True
        self.logger.info("strategy_start", nodes=len(start_data.get("nodes", []) or []), edges=len(start_data.get("edges", []) or []))

    def decide(self, state: GameState) -> ActionBundle:
        self._learn_from_feedback(state)
        self._update_station_tracking(state)
        if state.me.station != self._last_station and state.me.station is not None:
            self._completed_fixed_process_nodes.discard(state.me.station)
            self._pending_process_until.pop(state.me.station, None)
            self._pending_process_started_at.pop(state.me.station, None)
        self._last_station = state.me.station
        self._log_state_snapshot(state)
        decision = self._decide(state)
        self._remember_outbound_process(state, decision.bundle)
        if decision.bundle.squad is not None:
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

    def _optional_window_action(self, state: GameState) -> tuple[WindowAction | None, str | None]:
        window = state.active_window()
        if window is None:
            return None, None
        object_key = self._window_object_key(window) or f"WINDOW:{window.id}"
        status = str(window.status or "").upper()
        seen = self._window_seen.get(object_key, 0) + 1
        self._window_seen[object_key] = seen
        if object_key in self._suppressed_window_keys or status in WINDOW_TERMINAL_STATUSES:
            self.logger.info("stall_breaker", kind="window", station=window.target or state.me.station, objectKey=object_key, action="SUPPRESS", reason="窗口已熔断/已结束，不再发送 WINDOW_CARD")
            return None, None
        if seen > WINDOW_HARD_MAX_SENDS or window.round_index > 3:
            self._suppress_window(object_key, state, f"window_repeated:{seen}:roundIndex={window.round_index}")
            return None, None
        if self._is_object_on_cooldown(state, object_key) or self._is_station_escape_active(state, window.target or state.me.station):
            self._suppress_window(object_key, state, "window_on_cooldown_or_station_escape")
            return None, None
        choice = self.window_strategy.choose(state, window, self.config)
        self.logger.info("strategy_step", step="window_card", contestId=window.id, contestType=window.window_type, target=window.target, resourceType=window.resource_type, taskId=window.task_id, roundIndex=window.round_index, chosenCard=choice.card.value, windowStyle=choice.style, choiceReason=choice.reason, roll=choice.roll)
        return WindowAction(window.id, choice.card), f"window:{window.window_type}:{choice.card.value}"

    def _suppress_window(self, object_key: str, state: GameState, reason: str) -> None:
        self._suppressed_window_keys.add(object_key)
        self._cooldown_object(state, object_key, reason)
        self.logger.info("stall_breaker", kind="window", station=state.me.station, objectKey=object_key, action="SUPPRESS", reason=f"窗口熔断：{reason}；后续不再发送 WINDOW_CARD/ABSTAIN")

    def _attach_window(self, bundle: ActionBundle, window: WindowAction | None) -> ActionBundle:
        if window is None or bundle.window is not None:
            return bundle
        return ActionBundle(main=bundle.main, squad=bundle.squad, window=window, debug=bundle.debug)

    def _decide(self, state: GameState) -> Decision:
        me = state.me
        window_action, window_reason = self._optional_window_action(state)

        def done(bundle: ActionBundle, reason: str) -> Decision:
            reason_text = reason if window_reason is None else f"{reason}+{window_reason}"
            return Decision(self._attach_window(bundle, window_action), reason_text)

        if me.delivered or me.status == ConvoyStatus.DELIVERED:
            return done(wait("already_delivered", active=False), "already_delivered")
        if me.retired or me.status == ConvoyStatus.RETIRED:
            return done(wait("retired", active=False), "retired")
        if me.status in MOVING_STATES or self._is_transit_waiting(state):
            horse = self._moving_horse_action(state)
            if horse is not None:
                return done(horse, "use_horse_while_moving")
            return done(wait(f"moving:{me.status.value}", active=False), f"moving:{me.status.value}")
        if me.status in BUSY_STATES or me.current_process is not None:
            return done(wait(f"busy:{me.status.value}", active=False), f"busy:{me.status.value}")
        pending = self._pending_process_wait_action(state)
        if pending is not None:
            return done(pending, "wait_pending_process")
        fresh_action = self._freshness_action(state)
        if fresh_action is not None:
            return done(fresh_action, "use_ice_box")
        if me.station == state.terminal_node:
            if me.verified and me.good_fruit > 0 and me.freshness > 0:
                return done(ActionBundle(main=MainAction(MainActionType.DELIVER)), "deliver")
            return done(self._move_to(state, state.gate_node), "leave_terminal_not_ready")
        if me.station == state.gate_node:
            if not me.verified:
                if self._can_verify_gate(state):
                    return done(self._verify_action(state), "verify_gate")
                return done(wait("at_gate_before_rush", active=False), "at_gate_before_rush")
            return done(self._move_to(state, state.terminal_node), "gate_to_terminal")
        fixed_process = self._fixed_process_action(state)
        if fixed_process is not None:
            return done(fixed_process, "fixed_process")
        rush_tactic = self._rush_tactic_action(state)
        if rush_tactic is not None:
            return done(rush_tactic, "use_rush_tactic")
        intel_action = self._intel_action(state)
        if intel_action is not None:
            return done(intel_action, "use_intel")
        pre_move_resource = self._pre_move_resource_action(state)
        if pre_move_resource is not None:
            return done(pre_move_resource, "use_route_resource")
        if self._need_endgame(state) or self._opponent_pressure(state) or self._should_lock_delivery(state):
            self.logger.info("strategy_step", step="delivery_guard", reason="score_or_deadline_delivery_first")
            scout = self._squad_scout_action(state)
            return done(self._move_towards_delivery(state, squad=scout), "delivery_guard")
        if self._is_station_escape_active(state):
            self.logger.info("stall_breaker", kind="station", station=me.station, stayFrames=self._station_stay_frames(state), escapeUntil=self._station_escape_until.get(me.station or ""), action="MOVE_MAINLINE", reason="当前站点停留过久，暂停本地任务资源，直奔主线")
            scout = self._squad_scout_action(state)
            return done(self._move_towards_delivery(state, squad=scout), "station_stall_escape")
        station_task = self._best_station_task(state)
        if station_task is not None:
            return done(self._claim_task(station_task), f"claim_task:{station_task.template}:{station_task.id}")
        station_resource = self._best_station_resource(state)
        if station_resource is not None:
            return done(self._claim_resource(station_resource), f"claim_resource:{station_resource.resource_type}")
        scout = self._squad_scout_action(state)
        route_task = self._best_reachable_task(state)
        if route_task is not None:
            return done(self._move_towards_node(state, route_task.target, squad=scout), f"move_to_task:{route_task.template}:{route_task.id}")
        route_resource = self._best_reachable_resource(state)
        if route_resource is not None:
            return done(self._move_towards_node(state, route_resource.station, squad=scout), f"move_to_resource:{route_resource.resource_type}")
        return done(self._move_towards_delivery(state, squad=scout), "move_towards_delivery")

    def _learn_from_feedback(self, state: GameState) -> None:
        for event in state.events:
            if not isinstance(event, dict):
                continue
            if not self._record_belongs_to_me(state, event):
                self.logger.info("feedback_ignore", reason="opponent_event", eventPreview=str(event)[:300])
                continue
            event_type = str(event.get("event") or event.get("eventType") or event.get("type") or event.get("effectType") or "").upper()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            node_id = event.get("targetNodeId") or event.get("nodeId") or payload.get("targetNodeId") or payload.get("nodeId") or state.me.station
            task_id = event.get("taskId") or payload.get("taskId")
            resource_type = event.get("resourceType") or payload.get("resourceType")
            error_code = str(event.get("errorCode") or payload.get("errorCode") or event.get("code") or "").upper()
            action = str(event.get("action") or payload.get("action") or event.get("actionType") or "").upper()
            self._learn_error_code(state, action, error_code, node_id, task_id, resource_type, event)
            if event_type in {"PROCESS_COMPLETE", "FIXED_PROCESS_COMPLETE", "PROCESS_COMPLETED"} and node_id:
                self._mark_process_completed(str(node_id), state, event)
            if event_type in {"TASK_COMPLETE", "CLAIM_TASK_COMPLETE"} and task_id:
                self._rejected_task_ids.discard(str(task_id))
            if event_type in {"WINDOW_CONTEST_DRAW", "WINDOW_CONTEST_REPEAT_SUPPRESSED", "CONTEST_DRAW"}:
                object_key = self._event_object_key(event)
                if object_key is not None:
                    self._suppress_window(object_key, state, f"event:{event_type}")
        for result in state.action_results:
            if not isinstance(result, dict):
                continue
            if not self._record_belongs_to_me(state, result):
                self.logger.info("feedback_ignore", reason="opponent_action_result", resultPreview=str(result)[:300])
                continue
            action = str(result.get("action") or result.get("actionType") or result.get("type") or "").upper()
            accepted = result.get("accepted")
            success = result.get("success")
            effective = result.get("effective")
            code = str(result.get("code") or result.get("errorCode") or result.get("reason") or result.get("message") or "").upper()
            node_id = result.get("targetNodeId") or result.get("nodeId") or state.me.station
            task_id = result.get("taskId")
            resource_type = result.get("resourceType")
            self.logger.info("action_result", action=action, accepted=accepted, success=success, code=code, nodeId=node_id, taskId=task_id, resourceType=resource_type, raw=result)
            failed = accepted is False or success is False or effective is False or bool(code)
            if action == "WINDOW_CARD" and (failed or code in WINDOW_REJECT_CODES):
                contest_id = str(result.get("contestId") or result.get("windowId") or result.get("id") or "")
                if contest_id:
                    self._suppress_window(f"WINDOW:{contest_id}", state, f"reject:{code or 'WINDOW_CARD_FAILED'}")
                continue
            if failed:
                self._learn_error_code(state, action, code, node_id, task_id, resource_type, result)
            elif action == "PROCESS" and node_id:
                self._mark_process_pending(str(node_id), state, "accepted")

    def _record_belongs_to_me(self, state: GameState, record: dict[str, Any]) -> bool:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        player_values = [record.get(k) for k in ("playerId", "actorPlayerId", "sourcePlayerId", "ownerPlayerId")]
        player_values += [payload.get(k) for k in ("playerId", "actorPlayerId", "sourcePlayerId", "ownerPlayerId")]
        explicit_players = [v for v in player_values if v not in (None, "")]
        if explicit_players:
            return any(str(v) == str(state.player_id) for v in explicit_players)
        team_values = [record.get(k) for k in ("teamId", "actorTeamId", "sourceTeamId", "ownerTeamId")]
        team_values += [payload.get(k) for k in ("teamId", "actorTeamId", "sourceTeamId", "ownerTeamId")]
        explicit_teams = [v for v in team_values if v not in (None, "")]
        if explicit_teams and state.me.team_id is not None:
            return any(str(v) == str(state.me.team_id) for v in explicit_teams)
        return True

    def _learn_error_code(self, state: GameState, action: str, code: str, node_id: Any, task_id: Any, resource_type: Any, raw: dict[str, Any]) -> None:
        if not code:
            return
        node = str(node_id or state.me.station or "")
        if code in PROCESS_RETRY_CODES:
            if node:
                self._forced_process_nodes.add(node)
                self._rejected_fixed_process_nodes.discard(node)
                self._completed_fixed_process_nodes.discard(node)
                self._pending_process_until.pop(node, None)
                self._pending_process_started_at.pop(node, None)
                self._station_escape_until.pop(node, None)
                self.logger.info("feedback_learn", learned="process_required", nodeId=node, code=code, result=raw)
            return
        if code in PROCESS_HARD_REJECT_CODES or (action in {"PROCESS", "DOCK"} and code):
            if node:
                self._forced_process_nodes.discard(node)
                self._pending_process_until.pop(node, None)
                self._pending_process_started_at.pop(node, None)
                if code != "OBJECT_BUSY":
                    self._rejected_fixed_process_nodes.add(node)
                self.logger.info("feedback_learn", learned="fixed_process_rejected", nodeId=node, code=code, result=raw)
            return
        if action == "CLAIM_TASK" and task_id:
            self._rejected_task_ids.add(str(task_id))
            self._cooldown_object(state, self._task_object_key(str(task_id)), f"reject:{code}")
            self.logger.info("feedback_learn", learned="task_rejected", taskId=task_id, code=code, result=raw)
        if action == "CLAIM_RESOURCE" and node_id and resource_type:
            self._rejected_resource_keys.add((str(node_id), str(resource_type)))
            self._cooldown_object(state, self._resource_object_key(str(node_id), str(resource_type)), f"reject:{code}")
            self.logger.info("feedback_learn", learned="resource_rejected", nodeId=node_id, resourceType=resource_type, code=code, result=raw)

    def _mark_process_pending(self, node: str, state: GameState, reason: str) -> None:
        station = state.station(node)
        process_round = station.process_round if station is not None and station.process_round > 0 else 4
        until = state.frame + process_round + 3
        self._pending_process_until[node] = max(self._pending_process_until.get(node, 0), until)
        self._pending_process_started_at.setdefault(node, state.frame)
        self._completed_fixed_process_nodes.discard(node)
        self.logger.info("process_pending", station=node, until=until, processRound=process_round, reason=reason)

    def _mark_process_completed(self, node: str, state: GameState, raw: dict[str, Any]) -> None:
        self._completed_fixed_process_nodes.add(node)
        self._forced_process_nodes.discard(node)
        self._rejected_fixed_process_nodes.discard(node)
        self._pending_process_until.pop(node, None)
        self._pending_process_started_at.pop(node, None)
        self.logger.info("feedback_learn", learned="fixed_process_completed", nodeId=node, raw=raw)

    def _remember_outbound_process(self, state: GameState, bundle: ActionBundle) -> None:
        if bundle.main is None or bundle.main.action != MainActionType.PROCESS:
            return
        target = bundle.main.target or state.me.station
        if target:
            self._mark_process_pending(str(target), state, "outbound")

    def _pending_process_wait_action(self, state: GameState) -> ActionBundle | None:
        station = state.me.station
        if station is None or station not in self._pending_process_until:
            return None
        if station in self._completed_fixed_process_nodes:
            self._pending_process_until.pop(station, None)
            self._pending_process_started_at.pop(station, None)
            return None
        until = self._pending_process_until[station]
        if state.frame <= until:
            self.logger.info("process_pending_wait", station=station, startedAt=self._pending_process_started_at.get(station), until=until, reason="PROCESS 已提交，等待 PROCESS_COMPLETE，不重复提交，不移动离站")
            return wait("pending_process", active=False)
        self.logger.info("process_pending_timeout", station=station, startedAt=self._pending_process_started_at.get(station), until=until, reason="等待超时，允许重新提交 PROCESS")
        self._pending_process_until.pop(station, None)
        self._pending_process_started_at.pop(station, None)
        return None

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
            forcedProcessNodes=list(sorted(self._forced_process_nodes))[:5],
            completedProcessNodes=list(sorted(self._completed_fixed_process_nodes))[:5],
            pendingProcessNodes=dict(sorted(self._pending_process_until.items())),
            suppressedWindows=list(sorted(self._suppressed_window_keys))[:5],
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

    def _is_transit_waiting(self, state: GameState) -> bool:
        me = state.me
        if me.status != ConvoyStatus.WAITING:
            return False
        return bool(me.route_edge_id)

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
        rush = "BREAK_ORDER" if state.me.rush_tactic_used_count == 0 and state.phase in RUSH_PHASES else None
        return ActionBundle(main=MainAction(MainActionType.VERIFY_GATE, target=state.gate_node, rush_tactic=rush))

    def _rush_tactic_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if state.phase not in RUSH_PHASES or me.rush_tactic_used_count > 0:
            return None
        if me.station in {None, state.gate_node, state.terminal_node}:
            return None
        if me.status not in PLANNING_STATES or me.current_process is not None:
            return None
        if not me.has_buff("RUSH_PROTECT") and (me.task_score_base >= self.config.target_task_score or me.freshness <= 86):
            self.logger.info("rush_tactic", action="RUSH_PROTECT", reason="protect_freshness_in_rush", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.RUSH_PROTECT))
        if not me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") and me.good_fruit >= 88:
            target = state.terminal_node if me.verified else state.gate_node
            remaining_cost = self.route_planner.estimate_frames(state, me.station, target)
            if remaining_cost >= 8 and state.turns_left <= remaining_cost + 32:
                self.logger.info("rush_tactic", action="RUSH_SPEED", reason="deadline_speedup", remainingCost=remaining_cost, turnsLeft=state.turns_left)
                return ActionBundle(main=MainAction(MainActionType.RUSH_SPEED))
        return None

    def _freshness_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("ICE_BOX"):
            return None
        if me.freshness <= self.config.critical_freshness_threshold:
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="critical_freshness", freshness=me.freshness)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.freshness <= self.config.low_freshness_threshold and (me.task_score_base >= self.config.target_task_score // 2 or self._need_endgame(state)):
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="protect_scoring_run", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.freshness <= 82 and (me.task_score_base >= self.config.target_task_score or state.turns_left < 320):
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="protect_quality_before_delivery", freshness=me.freshness, taskScore=me.task_score_base, turnsLeft=state.turns_left)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        return None

    def _pre_move_resource_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.status not in PLANNING_STATES or me.station is None:
            return None
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        target = self._current_route_objective(state)
        remaining_cost = self.route_planner.estimate_frames(state, me.station, target)
        if remaining_cost >= 6 and me.has_resource("FAST_HORSE"):
            self.logger.info("resource_use", resourceType="FAST_HORSE", reason="pre_move_long_route", target=target, remainingCost=remaining_cost)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if remaining_cost >= 4 and me.has_resource("SHORT_HORSE") and (me.task_score_base >= self.config.target_task_score or state.turns_left < 360):
            self.logger.info("resource_use", resourceType="SHORT_HORSE", reason="pre_move_medium_route", target=target, remainingCost=remaining_cost)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _intel_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("INTEL") or me.status not in PLANNING_STATES or me.station is None:
            return None
        if me.squad_available > 0 and state.phase not in RUSH_PHASES:
            return None
        target = self._intel_target(state)
        if target is None:
            self.logger.info("resource_use_skip", resourceType="INTEL", reason="no_route_scout_target")
            return None
        self.logger.info("resource_use", resourceType="INTEL", reason="route_intel_scout", target=target)
        return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, target=target, resource_type="INTEL"))

    def _intel_target(self, state: GameState) -> str | None:
        forbidden = self._scout_forbidden(state)
        objective = self._current_route_objective(state)
        candidates = self._scout_path_candidates(state, objective, forbidden)
        return candidates[0] if candidates else None

    def _moving_horse_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        if me.has_resource("FAST_HORSE"):
            self.logger.info("resource_use", resourceType="FAST_HORSE", reason="moving_speedup")
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if me.has_resource("SHORT_HORSE"):
            delivery_target = state.terminal_node if me.verified else state.gate_node
            remaining_cost = self.route_planner.estimate_frames(state, me.station, delivery_target) if me.station else 10**9
            if state.turns_left < 360 or remaining_cost >= 4 or me.task_score_base >= self.config.target_task_score:
                self.logger.info("resource_use", resourceType="SHORT_HORSE", reason="moving_speedup", turnsLeft=state.turns_left, remainingCost=remaining_cost)
                return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _fixed_process_action(self, state: GameState) -> ActionBundle | None:
        station_id = state.me.station
        station = state.station(station_id)
        forced = station_id in self._forced_process_nodes if station_id is not None else False
        if not forced:
            if station is None or not station.process_type or station.process_round <= 0:
                return None
            if station.process_type == "VERIFY":
                return None
            if station.id in self._completed_fixed_process_nodes:
                self.logger.info("fixed_process_skip", station=station.id, processType=station.process_type, reason="already_completed_this_visit")
                return None
            if station.id in self._rejected_fixed_process_nodes:
                self.logger.info("fixed_process_skip", station=station.id, processType=station.process_type, reason="recently_rejected")
                return None
        if state.me.current_process is not None:
            return None
        target = station_id or (station.id if station else None)
        if target is None:
            return None
        process_type = station.process_type if station is not None else "UNKNOWN"
        reason = "server_process_required" if forced else "station_process_required"
        self.logger.info("fixed_process_eval", station=target, processType=process_type, action="PROCESS", reason=reason)
        return ActionBundle(main=MainAction(MainActionType.PROCESS, target=target))

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        tasks = [task for task in state.station_tasks(state.me.station) if task.id not in self._rejected_task_ids and not self._is_object_on_cooldown(state, self._task_object_key(task.id))]
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
        self.logger.info("task_eval_station", station=state.me.station, candidates=[{"taskId": t.id, "template": t.template, "score": t.score, "processFrames": t.process_frames, "rank": score(t)} for t in tasks], chosen=best.id)
        if state.me.task_score_base < self.config.target_task_score:
            return best
        if state.me.task_score_base < self.config.competitive_task_score and best.score >= 30 and not self._need_endgame(state):
            return best
        if best.score >= 45 and best.process_frames <= 6 and not self._need_endgame(state):
            return best
        return None

    def _best_station_resource(self, state: GameState) -> ResourceStock | None:
        stocks = [stock for stock in state.station_resources(state.me.station) if (stock.station, stock.resource_type) not in self._rejected_resource_keys and not self._is_object_on_cooldown(state, self._resource_object_key(stock.station, stock.resource_type))]
        if not stocks:
            self.logger.info("resource_eval_station", station=state.me.station, candidates=[])
            return None
        useful = [stock for stock in stocks if stock.resource_type in self.config.resource_priority]
        if self._need_endgame(state) or self._opponent_pressure(state):
            useful = [stock for stock in useful if stock.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE"}]
        if not useful:
            self.logger.info("resource_eval_station", station=state.me.station, candidates=[{"resourceType": s.resource_type, "amount": s.amount} for s in stocks], chosen=None)
            return None
        chosen = max(useful, key=lambda stock: self._resource_value(state, stock, detour=0))
        self.logger.info("resource_eval_station", station=state.me.station, candidates=[{"resourceType": s.resource_type, "amount": s.amount, "value": self._resource_value(state, s, detour=0)} for s in stocks], chosen=chosen.resource_type)
        return chosen

    def _best_reachable_task(self, state: GameState) -> TaskInstance | None:
        if state.me.task_score_base >= self.config.greed_task_score or state.me.station is None:
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        candidates: list[tuple[int, TaskInstance, int, int, int]] = []
        for task in state.tasks:
            if task.id in self._rejected_task_ids or self._is_object_on_cooldown(state, self._task_object_key(task.id)):
                continue
            if not task.available_for(state.player_id) or task.score <= 0:
                continue
            to_task = self.route_planner.estimate_frames(state, state.me.station, task.target)
            to_gate = self.route_planner.estimate_frames(state, task.target, state.gate_node)
            detour = to_task + task.process_frames + to_gate - direct
            max_detour = self.config.max_task_detour_frames + (12 if task.score >= 30 else 0)
            if state.me.task_score_base >= self.config.target_task_score and task.score >= 30:
                max_detour = max(max_detour, self.config.max_competitive_task_detour_frames)
            if detour <= max_detour:
                value = task.score * 4 - max(0, detour)
                if task.score >= 30:
                    value += 40
                if state.me.task_score_base >= self.config.target_task_score:
                    value += 20
                candidates.append((value, task, detour, to_task, to_gate))
        if not candidates:
            self.logger.info("task_eval_reachable", directToGate=direct, candidates=[])
            return None
        chosen_value, chosen, chosen_detour, chosen_to_task, chosen_to_gate = max(candidates, key=lambda item: item[0])
        self.logger.info("task_eval_reachable", directToGate=direct, candidates=[{"taskId": t.id, "template": t.template, "target": t.target, "score": t.score, "value": v, "detour": d, "toTask": tt, "toGate": tg} for v, t, d, tt, tg in sorted(candidates, key=lambda item: item[0], reverse=True)[:5]], chosen=chosen.id, chosenValue=chosen_value, chosenDetour=chosen_detour, chosenToTask=chosen_to_task, chosenToGate=chosen_to_gate)
        return chosen

    def _best_reachable_resource(self, state: GameState) -> ResourceStock | None:
        if state.me.station is None:
            return None
        if self._opponent_pressure(state):
            self.logger.info("resource_eval_reachable", candidates=[], reason="opponent_pressure")
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        candidates: list[tuple[int, ResourceStock, int]] = []
        for stock in state.resources:
            if (stock.station, stock.resource_type) in self._rejected_resource_keys or self._is_object_on_cooldown(state, self._resource_object_key(stock.station, stock.resource_type)):
                continue
            if stock.resource_type not in self.config.resource_priority:
                continue
            to_res = self.route_planner.estimate_frames(state, state.me.station, stock.station)
            to_gate = self.route_planner.estimate_frames(state, stock.station, state.gate_node)
            detour = to_res + stock.claim_frames + to_gate - direct
            max_detour = self.config.max_resource_detour_frames
            if stock.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE"}:
                max_detour = max(max_detour, self.config.max_valuable_resource_detour_frames)
            if detour <= max_detour:
                candidates.append((self._resource_value(state, stock, detour=detour), stock, detour))
        if not candidates:
            self.logger.info("resource_eval_reachable", directToGate=direct, candidates=[])
            return None
        chosen_value, chosen, chosen_detour = max(candidates, key=lambda item: item[0])
        self.logger.info("resource_eval_reachable", directToGate=direct, candidates=[{"resourceType": s.resource_type, "station": s.station, "value": v, "detour": d} for v, s, d in sorted(candidates, key=lambda item: item[0], reverse=True)[:5]], chosen=chosen.resource_type, chosenStation=chosen.station, chosenValue=chosen_value, chosenDetour=chosen_detour)
        return chosen

    def _resource_value(self, state: GameState, stock: ResourceStock, detour: int) -> int:
        priority = {name: i for i, name in enumerate(self.config.resource_priority)}
        base = 100 - priority.get(stock.resource_type, 999) * 8
        me = state.me
        if stock.resource_type == "ICE_BOX":
            base += 45 if me.freshness <= 88 else 20
            if me.task_score_base >= self.config.target_task_score:
                base += 25
        elif stock.resource_type == "FAST_HORSE":
            target = state.terminal_node if me.verified else state.gate_node
            remaining = self.route_planner.estimate_frames(state, stock.station, target)
            base += min(45, max(0, remaining * 5))
        elif stock.resource_type == "SHORT_HORSE":
            target = state.terminal_node if me.verified else state.gate_node
            remaining = self.route_planner.estimate_frames(state, stock.station, target)
            base += min(28, max(0, remaining * 4))
        elif stock.resource_type in {"PASS_TOKEN", "OFFICIAL_PERMIT", "BOAT_RIGHT"}:
            base += 18 if not me.verified else 6
        return base - max(0, detour * 2)

    def _squad_scout_action(self, state: GameState) -> SquadAction | None:
        if state.phase in RUSH_PHASES or state.me.squad_available <= 0 or state.me.station is None:
            return None
        forbidden = self._scout_forbidden(state)
        objective = self._scout_objective(state)
        path_candidates = self._scout_path_candidates(state, objective, forbidden)
        for target in path_candidates:
            self.logger.info("squad_eval", action="SQUAD_SCOUT", target=target, reason="route_path_scout", objective=objective, candidates=path_candidates)
            return SquadAction(SquadActionType.SQUAD_SCOUT, target)
        fallback_candidates: list[str] = []
        for target in self.config.scout_targets:
            if target in forbidden:
                self.logger.info("squad_eval_skip", target=target, reason="forbidden_scout_target")
                continue
            if target in self._scout_dispatched or self._has_own_scout_marker(state, target):
                continue
            if self.route_planner.estimate_frames(state, state.me.station, target) < 10**8:
                fallback_candidates.append(target)
        if fallback_candidates:
            target = min(fallback_candidates, key=lambda node: self.route_planner.estimate_frames(state, state.me.station, node))
            self.logger.info("squad_eval", action="SQUAD_SCOUT", target=target, reason="fallback_nearest_config_target", objective=objective, candidates=fallback_candidates)
            return SquadAction(SquadActionType.SQUAD_SCOUT, target)
        self.logger.info("squad_eval", action=None, reason="no_available_scout_target", objective=objective)
        return None

    def _scout_forbidden(self, state: GameState) -> set[str]:
        return {state.me.station or "", state.start_node, state.gate_node, state.terminal_node, *map(str, state.roles.get("safeZoneNodeIds", []) or [])}

    def _scout_objective(self, state: GameState) -> str:
        me = state.me
        if self._need_endgame(state) or self._opponent_pressure(state) or self._should_lock_delivery(state):
            return state.terminal_node if me.verified else state.gate_node
        route_task = self._best_reachable_task(state)
        if route_task is not None:
            return route_task.target
        route_resource = self._best_reachable_resource(state)
        if route_resource is not None:
            return route_resource.station
        return state.terminal_node if me.verified else state.gate_node

    def _current_route_objective(self, state: GameState) -> str:
        if self._need_endgame(state) or self._opponent_pressure(state) or self._should_lock_delivery(state):
            return state.terminal_node if state.me.verified else state.gate_node
        route_task = self._best_reachable_task(state)
        if route_task is not None:
            return route_task.target
        route_resource = self._best_reachable_resource(state)
        if route_resource is not None:
            return route_resource.station
        return state.terminal_node if state.me.verified else state.gate_node

    def _should_lock_delivery(self, state: GameState) -> bool:
        me = state.me
        if me.task_score_base >= self.config.competitive_task_score:
            return True
        if me.good_fruit < 78 or me.freshness < 68:
            return me.task_score_base >= self.config.target_task_score
        return False

    def _scout_path_candidates(self, state: GameState, objective: str, forbidden: set[str]) -> list[str]:
        plan = self.route_planner.plan(state, state.me.station, objective)
        if plan is None:
            return []
        result: list[str] = []
        for node in plan.path[1 : 1 + SCOUT_PATH_LOOKAHEAD]:
            if node in forbidden:
                continue
            if node in self._scout_dispatched or self._has_own_scout_marker(state, node):
                continue
            result.append(node)
        return result

    def _has_own_scout_marker(self, state: GameState, target: str) -> bool:
        station = state.station(target)
        if station is None:
            return False
        markers = station.raw.get("scouted")
        if not isinstance(markers, list):
            return False
        return any(isinstance(marker, dict) and marker.get("teamId") == state.me.team_id and marker.get("remainingTriggers", 1) for marker in markers)

    def _claim_task(self, task: TaskInstance) -> ActionBundle:
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
        if state.me.status not in PLANNING_STATES:
            return
        if self._is_mainline_station(state, station) or station in self._forced_process_nodes or station in self._pending_process_until:
            return
        stay_frames = self._station_stay_frames(state)
        if stay_frames < self.config.station_stall_frames or self._is_station_escape_active(state, station):
            return
        until = state.frame + self.config.station_escape_frames
        self._station_escape_until[station] = until
        self.logger.info("stall_breaker", kind="station", station=station, stayFrames=stay_frames, escapeUntil=until, action="ARM_ESCAPE", reason="同一站点停留过久，疑似任务/资源争抢循环")

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
        self.logger.info("stall_breaker", kind="object", objectKey=object_key, cooldownUntil=until, reason=reason)

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
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        task_id = event.get("taskId") or payload.get("taskId")
        if task_id:
            return self._task_object_key(str(task_id))
        node_id = event.get("targetNodeId") or event.get("nodeId") or payload.get("targetNodeId") or payload.get("nodeId")
        resource_type = event.get("resourceType") or payload.get("resourceType")
        if node_id and resource_type:
            return self._resource_object_key(str(node_id), str(resource_type))
        contest_id = event.get("contestId") or payload.get("contestId")
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
            support = self._squad_blocker_action(state, target, "obstacle") or squad
            if support is not None and support is not squad:
                self.logger.info("blocker_decision", target=target, blocker="obstacle", action="SQUAD_CLEAR", reason="save_good_fruit")
                return ActionBundle(squad=support)
            if state.me.good_fruit > 5:
                self.logger.info("blocker_decision", target=target, blocker="obstacle", action="CLEAR")
                return ActionBundle(main=MainAction(MainActionType.CLEAR, target=target), squad=support)
            self.logger.info("blocker_decision", target=target, blocker="obstacle", action="FORCED_PASS")
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=support)
        if station is not None and station.has_enemy_guard(state.me.team_id):
            if state.me.bad_fruit >= 2 or state.me.good_fruit >= 95:
                self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="BREAK_GUARD")
                return ActionBundle(main=MainAction(MainActionType.BREAK_GUARD, target=target, good_fruit=0 if state.me.bad_fruit >= 2 else 1, bad_fruit=min(2, state.me.bad_fruit)), squad=squad)
            support = self._squad_blocker_action(state, target, "enemy_guard") or squad
            if support is not None and support is not squad:
                self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="SQUAD_WEAKEN", reason="save_good_fruit")
                return ActionBundle(squad=support)
            self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="FORCED_PASS")
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=support)
        self.logger.info("move_decision", target=target, action="MOVE")
        return ActionBundle(main=MainAction(MainActionType.MOVE, target=target), squad=squad)

    def _squad_blocker_action(self, state: GameState, target: str, blocker: str) -> SquadAction | None:
        if state.phase in RUSH_PHASES or state.me.squad_available <= 0:
            return None
        if target in self._scout_dispatched or self._has_own_scout_marker(state, target):
            return None
        if blocker == "obstacle":
            self.logger.info("squad_eval", action="SQUAD_CLEAR", target=target, reason="blocked_route_obstacle")
            return SquadAction(SquadActionType.SQUAD_CLEAR, target)
        if blocker == "enemy_guard":
            self.logger.info("squad_eval", action="SQUAD_WEAKEN", target=target, reason="blocked_route_enemy_guard")
            return SquadAction(SquadActionType.SQUAD_WEAKEN, target)
        return None

    def _t04_for_target(self, state: GameState, target: str) -> TaskInstance | None:
        for task in state.tasks:
            if task.template == "T04" and task.target == target and task.available_for(state.player_id) and task.id not in self._rejected_task_ids:
                return task
        return None
