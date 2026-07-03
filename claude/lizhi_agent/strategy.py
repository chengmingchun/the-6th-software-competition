from __future__ import annotations

"""Self-contained Claude strategy (v2 — fixed).

Fixes applied:
  P0-1: _should_lock_delivery no longer locks on freshness alone before task_score>=90.
  P0-2: _window_action is no longer called unconditionally every frame — it's guarded.
  P0-3: obstacle / enemy_guard handling in _move_to transplanted from root Baseline.
  P1-1: CLAIM_TASK no longer sends extraneous targetNodeId.
  P1-2: Window counter-strategy (opponent-revealed card detection) from root.
  P1-3: T04 tasks use neighbor-approach candidates, not raw task.target.
  P1-4: Resource detour caps aligned with root (18/8).
  P1-5: _emergency_move never returns a no-main-action bundle.
  P2-2: station-stall escape + object-cooldown mechanics.
  P2-4: Gate wait uses active=False.
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
PLANNING_STATES = {ConvoyStatus.IDLE, ConvoyStatus.WAITING, ConvoyStatus.UNKNOWN, ConvoyStatus.COST_BANKRUPT}
ROUTE_RESOURCE_TYPES = {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL"}
HIGH_VALUE_RESOURCES = {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL", "PASS_TOKEN", "OFFICIAL_PERMIT"}
HIGH_VALUE_WINDOW_TYPES = {"GATE", "TASK", "PASS", "PROCESS", "FIXED_PROCESS", "VERIFY_GATE", "RESOURCE"}
PROCESS_RETRY_CODES = {"PROCESS_REQUIRED", "PROCESS_INTERRUPTED", "INTERRUPTED"}
PROCESS_HARD_REJECT_CODES = {"PROCESS_NOT_AVAILABLE", "NOT_AT_TARGET_NODE", "INVALID_TARGET"}
WINDOW_REJECT_CODES = {
    "WINDOW_NOT_ACTIVE", "WINDOW_NOT_AVAILABLE", "WINDOW_NOT_YOUR_TURN",
    "WINDOW_CARD_INVALID", "WINDOW_DRAW_RETRY_LIMIT", "CONTEST_NOT_ACTIVE",
    "CONTEST_NOT_FOUND", "INVALID_CONTEST", "INVALID_ACTION",
}
WINDOW_TERMINAL_STATUSES = {"SUPPRESSED", "RESOLVED", "FINISHED", "FINISH", "ENDED", "END",
                             "CLOSED", "COMPLETED", "COMPLETE", "SETTLED"}
WINDOW_HARD_MAX_SENDS = 3
SCOUT_PATH_LOOKAHEAD = 3

# Conservative fallback route — only when graph planner fails (malformed edges).
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
        # Station stall / cooldown (from root)
        self._station_since_frame: int | None = None
        self._station_escape_until: dict[str, int] = {}
        self._object_cooldown_until: dict[str, int] = {}
        self._window_seen: dict[str, int] = {}
        self._suppressed_window_keys: set[str] = set()
        self._forced_process_nodes: set[str] = set()
        self._pending_process_until: dict[str, int] = {}
        self._pending_process_started_at: dict[str, int] = {}
        self._rejected_fixed_process_nodes: set[str] = set()
        self._task_approach_nodes: dict[str, str] = {}

    def on_start(self, start_data: dict[str, Any]) -> None:
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
        try:
            bundle, reason = self._decide(state)
        except Exception as exc:
            self.logger.info("message_error", round=state.frame, error=repr(exc))
            bundle = wait("exception_fallback", active=False)
            reason = "exception_fallback"
        if bundle.squad is not None and bundle.squad.action == SquadActionType.SQUAD_SCOUT and bundle.squad.target:
            self._scout_dispatched.add(bundle.squad.target)
        self.logger.info(
            "decision",
            round=state.frame, phase=state.phase,
            station=state.me.station, status=state.me.status.value,
            score=state.me.total_score, taskScore=state.me.task_score_base,
            freshness=state.me.freshness, goodFruit=state.me.good_fruit,
            resources=state.me.resources, reason=reason,
            actions=bundle.to_actions(),
        )
        return bundle

    def _decide(self, state: GameState) -> tuple[ActionBundle, str]:
        me = state.me

        # -- TERMINAL GUARD --
        if me.delivered or me.status == ConvoyStatus.DELIVERED:
            return wait("delivered", active=False), "delivered"

        # -- MOVING GUARD --
        if me.status in MOVING_STATES or self._is_transit_waiting(state):
            horse = self._horse_action(state, reason="moving_speed")
            if horse is not None:
                return horse, "use_horse_while_moving"
            return wait("moving", active=False), f"moving:{me.status.value}"

        # -- BUSY GUARD --
        if me.status in BUSY_STATES or me.current_process is not None:
            return wait("busy", active=False), f"busy:{me.status.value}"

        # -- PENDING PROCESS WAIT --
        pending = self._pending_process_wait_action(state)
        if pending is not None:
            return pending, "wait_pending_process"

        # -- FRESHNESS (ICE_BOX) --
        ice = self._ice_box_action(state)
        if ice is not None:
            return ice, "use_ice_box"

        # -- S14 / S15 special nodes --
        if me.station == state.terminal_node:
            if me.verified and me.good_fruit > 0 and me.freshness > 0:
                return ActionBundle(main=MainAction(MainActionType.DELIVER)), "deliver"
            return self._move_to(state, state.gate_node), "terminal_not_ready_back_to_gate"

        if me.station == state.gate_node:
            if not me.verified:
                if self._can_verify_gate(state):
                    tactic = "BREAK_ORDER" if me.rush_tactic_used_count == 0 else None
                    return ActionBundle(main=MainAction(MainActionType.VERIFY_GATE, target=state.gate_node, rush_tactic=tactic)), "verify_gate"
                return wait("wait_gate_rush", active=False), "wait_gate_rush"
            return self._move_to(state, state.terminal_node), "gate_to_terminal"

        # -- FIXED PROCESS --
        fixed = self._fixed_process_action(state)
        if fixed is not None:
            return fixed, "fixed_process"

        # -- RUSH TACTIC (RUSH_SPEED / RUSH_PROTECT) --
        rush_tactic = self._rush_tactic_action(state)
        if rush_tactic is not None:
            return rush_tactic, "use_rush_tactic"

        # -- PRE-MOVE RESOURCE (horse before long route) --
        pre = self._pre_move_resource_action(state)
        if pre is not None:
            return pre, "use_route_resource"

        # -- DELIVERY GUARD (score/pressure based) --
        if self._need_endgame(state) or self._opponent_pressure(state):
            self.logger.info("strategy_step", step="delivery_guard", reason="score_or_deadline_delivery_first")
            return self._move_towards_delivery(state, squad=self._scout_action(state)), "delivery_guard"

        # -- STATION STALL ESCAPE --
        if self._is_station_escape_active(state):
            self.logger.info("stall_breaker", kind="station", station=me.station,
                             stayFrames=self._station_stay_frames(state),
                             escapeUntil=self._station_escape_until.get(me.station or ""),
                             action="MOVE_MAINLINE", reason="station_stall_escape")
            return self._move_towards_delivery(state, squad=self._scout_action(state)), "station_stall_escape"

        # -- WINDOW (only now, when we're at a PLANNING decision point) --
        window_action = self._window_action(state)

        def attach(b: ActionBundle) -> ActionBundle:
            if window_action is None or b.window is not None:
                return b
            return ActionBundle(main=b.main, squad=b.squad, window=window_action, debug=b.debug)

        # -- STATION TASK --
        task = self._best_station_task(state)
        if task is not None:
            bundle = ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, task_id=task.id), squad=self._scout_action(state, after_current_action=True))
            return attach(bundle), f"claim_task:{task.template}:{task.id}"

        # -- STATION RESOURCE --
        resource = self._best_station_resource(state)
        if resource is not None:
            bundle = ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=resource.station, resource_type=resource.resource_type), squad=self._scout_action(state, after_current_action=True))
            return attach(bundle), f"claim_resource:{resource.resource_type}"

        # -- USE INTEL (only if no better station action) --
        intel = self._intel_action(state)
        if intel is not None:
            return attach(intel), "use_intel"

        # -- REACHABLE TASK (detour) --
        task2 = self._best_reachable_task(state)
        if task2 is not None:
            approach = self._task_approach_nodes.get(task2.id, task2.target)
            if approach == state.me.station:
                bundle = ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, task_id=task2.id), squad=self._scout_action(state))
                return attach(bundle), f"claim_task:{task2.template}:{task2.id}:approach"
            return attach(self._move_to(state, approach, squad=self._scout_action(state))), f"move_to_task:{task2.template}:{task2.id}"

        # -- REACHABLE RESOURCE (detour) --
        resource2 = self._best_reachable_resource(state)
        if resource2 is not None:
            return attach(self._move_to(state, resource2.station, squad=self._scout_action(state))), f"move_to_resource:{resource2.resource_type}"

        # -- OPPORTUNISTIC GUARD (chokepoint blocking) --
        guard_action = self._opportunistic_guard_action(state)
        if guard_action is not None:
            return attach(guard_action), "opportunistic_guard"

        # -- DEFAULT: move towards delivery --
        return attach(self._move_towards_delivery(state, squad=self._scout_action(state))), "move_towards_delivery"

    # ── Feedback & Learning ──

    def _learn_from_feedback(self, state: GameState) -> None:
        for result in state.action_results:
            if not isinstance(result, dict):
                continue
            accepted = result.get("accepted", result.get("success", True))
            code = str(result.get("code") or result.get("errorCode") or result.get("reason") or "").upper()
            action = str(result.get("action") or result.get("actionType") or "").upper()
            node_id = str(result.get("targetNodeId") or result.get("nodeId") or state.me.station or "")
            task_id = result.get("taskId")
            resource_type = result.get("resourceType")
            failed = accepted is False

            if action == "WINDOW_CARD" and (failed or code in WINDOW_REJECT_CODES):
                contest_id = str(result.get("contestId") or result.get("windowId") or result.get("id") or "")
                if contest_id:
                    self._suppress_window(f"WINDOW:{contest_id}", state, f"reject:{code or 'WINDOW_CARD_FAILED'}")
                continue

            if failed:
                self._learn_error_code(state, action, code, node_id, task_id, resource_type, result)
            elif action == "PROCESS" and node_id:
                self._mark_process_pending(str(node_id), state, "accepted")

        for event in state.events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event") or event.get("eventType") or event.get("type") or "").upper()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            node_id = str(event.get("targetNodeId") or event.get("nodeId") or payload.get("targetNodeId") or payload.get("nodeId") or state.me.station or "")
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

    def _learn_error_code(self, state: GameState, action: str, code: str, node_id: str, task_id: Any, resource_type: Any, raw: dict[str, Any]) -> None:
        if not code:
            return
        node = node_id or state.me.station or ""
        if code in PROCESS_RETRY_CODES:
            if node:
                self._forced_process_nodes.add(node)
                self._rejected_fixed_process_nodes.discard(node)
                self._completed_fixed_process_nodes.discard(node)
                self._pending_process_until.pop(node, None)
                self._pending_process_started_at.pop(node, None)
                self._station_escape_until.pop(node, None)
            return
        if code in PROCESS_HARD_REJECT_CODES or (action in {"PROCESS", "DOCK"} and code):
            if node:
                self._forced_process_nodes.discard(node)
                self._pending_process_until.pop(node, None)
                self._pending_process_started_at.pop(node, None)
                if code != "OBJECT_BUSY":
                    self._rejected_fixed_process_nodes.add(node)
            return
        if action == "CLAIM_TASK" and task_id:
            self._rejected_task_ids.add(str(task_id))
            self._cooldown_object(state, self._task_object_key(str(task_id)), f"reject:{code}")
        elif action in ("CLAIM_RESOURCE", "CLAIM_RSOURCE") and node_id and resource_type:
            self._rejected_resource_keys.add((str(node_id), str(resource_type)))
            self._cooldown_object(state, self._resource_object_key(str(node_id), str(resource_type)), f"reject:{code}")

    def _mark_process_pending(self, node: str, state: GameState, reason: str) -> None:
        station = state.station(node)
        process_round = station.process_round if station is not None and station.process_round > 0 else 4
        until = state.frame + process_round + 3
        self._pending_process_until[node] = max(self._pending_process_until.get(node, 0), until)
        self._pending_process_started_at.setdefault(node, state.frame)
        self._completed_fixed_process_nodes.discard(node)

    def _mark_process_completed(self, node: str, state: GameState, raw: dict[str, Any]) -> None:
        self._completed_fixed_process_nodes.add(node)
        self._forced_process_nodes.discard(node)
        self._rejected_fixed_process_nodes.discard(node)
        self._pending_process_until.pop(node, None)
        self._pending_process_started_at.pop(node, None)

    def _pending_process_wait_action(self, state: GameState) -> ActionBundle | None:
        station = state.me.station
        if station is None or station not in self._pending_process_until:
            return None
        if station in self._completed_fixed_process_nodes:
            self._pending_process_until.pop(station, None)
            self._pending_process_started_at.pop(station, None)
            return None
        until = self._pending_process_until[station]
        started_at = self._pending_process_started_at.get(station, state.frame)
        server_confirms_processing = state.me.current_process is not None or state.me.status in BUSY_STATES
        if not server_confirms_processing and state.frame > started_at + self.config.process_start_grace_frames:
            self._pending_process_until.pop(station, None)
            self._pending_process_started_at.pop(station, None)
            return None
        if state.frame <= until:
            return wait("pending_process", active=False)
        self._pending_process_until.pop(station, None)
        self._pending_process_started_at.pop(station, None)
        return None

    # ── Window Strategy ──

    def _window_action(self, state: GameState) -> WindowAction | None:
        window = state.active_window()
        if window is None:
            return None
        object_key = self._window_object_key(window) or f"WINDOW:{window.id}"
        status = str(window.status or "").upper()
        seen = self._window_seen.get(object_key, 0) + 1
        self._window_seen[object_key] = seen
        if object_key in self._suppressed_window_keys or status in WINDOW_TERMINAL_STATUSES:
            self.logger.info("stall_breaker", kind="window", station=window.target or state.me.station,
                             objectKey=object_key, action="SUPPRESS",
                             reason="window_suppressed_or_ended")
            return None
        if seen > WINDOW_HARD_MAX_SENDS or window.round_index > 3:
            self._suppress_window(object_key, state, f"window_repeated:{seen}:roundIndex={window.round_index}")
            return None
        if self._is_object_on_cooldown(state, object_key) or self._is_station_escape_active(state, window.target or state.me.station):
            self._suppress_window(object_key, state, "window_on_cooldown_or_station_escape")
            return None

        card, reason = self._choose_window_card(state, window)
        self.logger.info("strategy_step", step="window_card", contestId=window.id,
                         contestType=window.window_type, target=window.target,
                         resourceType=window.resource_type, taskId=window.task_id,
                         roundIndex=window.round_index, chosenCard=card.value,
                         windowStyle="targeted", choiceReason=reason)
        return WindowAction(window.id, card)

    def _suppress_window(self, object_key: str, state: GameState, reason: str) -> None:
        self._suppressed_window_keys.add(object_key)
        self._cooldown_object(state, object_key, reason)

    def _choose_window_card(self, state: GameState, window: WindowState) -> tuple[WindowCard, str]:
        high = self._is_high_value_window(state, window)
        me = state.me
        if not high:
            return WindowCard.ABSTAIN, "low_value"

        # Counter known opponent card (from root WindowStrategy)
        opponent_card = self._opponent_revealed_card(state, window)
        if opponent_card is not None:
            counter = self._counter_card(state, opponent_card, high)
            if counter is not None:
                return counter, f"counter:{opponent_card.value}"

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

    # ── Resource Actions ──

    def _ice_box_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("ICE_BOX"):
            return None
        if me.freshness <= self.config.critical_freshness_threshold:
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX",
                             reason="critical_freshness", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.freshness <= self.config.low_freshness_threshold and (me.task_score_base >= self.config.target_task_score // 2 or self._need_endgame(state)):
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX",
                             reason="protect_scoring_run", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.freshness <= 82 and (me.task_score_base >= self.config.target_task_score or state.turns_left < 320):
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX",
                             reason="protect_quality_before_delivery", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.freshness <= 90 and me.task_score_base >= self.config.competitive_task_score:
            self.logger.info("resource_use", round=state.frame, resourceType="ICE_BOX",
                             reason="protect_high_score_quality", freshness=me.freshness, taskScore=me.task_score_base)
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
        if me.status not in PLANNING_STATES or me.station is None or me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        target = self._current_route_objective(state)
        remaining = self.route_planner.estimate_frames(state, me.station, target)
        if remaining >= 6 and me.has_resource("FAST_HORSE"):
            self.logger.info("resource_use", resourceType="FAST_HORSE", reason="pre_move_long_route", target=target, remainingCost=remaining)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if remaining >= 4 and me.has_resource("SHORT_HORSE") and (me.task_score_base >= self.config.target_task_score or state.turns_left < 360):
            self.logger.info("resource_use", resourceType="SHORT_HORSE", reason="pre_move_medium_route", target=target, remainingCost=remaining)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
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

    def _rush_tactic_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if state.phase not in RUSH_PHASES or me.rush_tactic_used_count > 0:
            return None
        if me.station in {None, state.gate_node, state.terminal_node}:
            return None
        if me.status not in PLANNING_STATES or me.current_process is not None:
            return None
        target = state.terminal_node if me.verified else state.gate_node
        remaining_cost = self.route_planner.estimate_frames(state, me.station, target)
        if not me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") and not me.has_resource("FAST_HORSE") and not me.has_resource("SHORT_HORSE") and me.good_fruit >= 88 and me.freshness >= 88:
            if remaining_cost >= 8 and state.turns_left <= remaining_cost + 32:
                self.logger.info("rush_tactic", action="RUSH_SPEED", reason="deadline_speedup", remainingCost=remaining_cost, turnsLeft=state.turns_left)
                return ActionBundle(main=MainAction(MainActionType.RUSH_SPEED))
        if not me.has_buff("RUSH_PROTECT") and (me.task_score_base >= self.config.target_task_score or me.freshness <= 86):
            self.logger.info("rush_tactic", action="RUSH_PROTECT", reason="protect_freshness_in_rush", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.RUSH_PROTECT))
        return None

    # ── Fixed Process ──

    def _fixed_process_action(self, state: GameState) -> ActionBundle | None:
        station_id = state.me.station
        station = state.station(station_id)
        forced = station_id in self._forced_process_nodes if station_id is not None else False
        if not forced:
            if station is None or not station.process_type or station.process_type == "VERIFY" or station.process_round <= 0:
                return None
            if station_id in self._completed_fixed_process_nodes:
                return None
            if station_id in self._rejected_fixed_process_nodes:
                return None
        if state.me.current_process is not None:
            return None
        target = station_id or (station.id if station else None)
        if target is None:
            return None
        process_type = station.process_type if station is not None else "UNKNOWN"
        reason_str = "server_required" if forced else "station_required"
        self.logger.info("fixed_process_eval", round=state.frame, station=target, processType=process_type, action="PROCESS", reason=reason_str)
        return ActionBundle(main=MainAction(MainActionType.PROCESS, target=target))

    def _can_verify_gate(self, state: GameState) -> bool:
        return state.phase in RUSH_PHASES

    # ── Delivery / Endgame Guards ──

    def _should_lock_delivery(self, state: GameState) -> bool:
        me = state.me
        if me.task_score_base < self.config.target_task_score:
            return False
        if state.phase in RUSH_PHASES or self._need_endgame(state):
            return True
        if me.task_score_base >= self.config.greed_task_score:
            return True
        if me.good_fruit < 78 or me.freshness < 68:
            return me.task_score_base >= self.config.target_task_score
        return False

    def _need_endgame(self, state: GameState) -> bool:
        me = state.me
        if me.station is None:
            return False
        target = state.terminal_node if me.verified else state.gate_node
        cost = self.route_planner.estimate_frames(state, me.station, target)
        if cost >= 10**8:
            cost = self._route_steps_fallback(me.station, target) * 50
        return state.turns_left <= cost + self.config.endgame_buffer_frames

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

    def _current_route_objective(self, state: GameState) -> str:
        if self._need_endgame(state) or self._opponent_pressure(state) or self._should_lock_delivery(state):
            return state.terminal_node if state.me.verified else state.gate_node
        task = self._best_reachable_task(state)
        if task is not None:
            return self._task_approach_nodes.get(task.id, task.target)
        resource = self._best_reachable_resource(state)
        if resource is not None:
            return resource.station
        return state.terminal_node if state.me.verified else state.gate_node

    # ── Task / Resource Evaluation ──

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        tasks = [t for t in state.station_tasks(state.me.station)
                 if t.id not in self._rejected_task_ids
                 and not self._is_object_on_cooldown(state, self._task_object_key(t.id))]
        if not tasks:
            return None
        if state.me.task_score_base >= self.config.greed_task_score and not self._need_endgame(state):
            return None

        def score(t: TaskInstance) -> tuple[int, int]:
            threshold_bonus = 100 if state.me.task_score_base < self.config.target_task_score and t.score >= 30 else 0
            clear_bonus = 20 if t.template == "T04" else 0
            return threshold_bonus + clear_bonus + t.score, -t.process_frames
        best = max(tasks, key=score)
        if state.me.task_score_base < self.config.target_task_score:
            return best
        if state.me.task_score_base < self.config.competitive_task_score and best.score >= 30 and not self._need_endgame(state):
            return best
        if best.score >= 45 and best.process_frames <= 6 and not self._need_endgame(state):
            return best
        return None

    def _best_reachable_task(self, state: GameState, *, exclude_current_station: bool = False) -> TaskInstance | None:
        me = state.me
        if me.station is None:
            return None
        if self._should_lock_delivery(state):
            return None
        direct = self._estimate_frames(state, me.station, state.gate_node)
        candidates: list[tuple[int, TaskInstance, str, int]] = []
        for task in state.tasks:
            if task.id in self._rejected_task_ids or self._is_object_on_cooldown(state, self._task_object_key(task.id)):
                continue
            if not task.available_for(state.player_id) or task.score <= 0:
                continue
            for approach in self._task_approach_candidates(state, task):
                if exclude_current_station and approach == me.station:
                    continue
                to_task = self._estimate_frames(state, me.station, approach)
                to_gate = self._estimate_frames(state, approach, state.gate_node)
                detour = to_task + task.process_frames + to_gate - direct
                max_detour = self.config.max_task_detour_frames + (12 if task.score >= 30 else 0)
                if task.template == "T04" and task.score >= 30:
                    max_detour += 18
                if me.task_score_base >= self.config.target_task_score and task.score >= 30:
                    max_detour = max(max_detour, self.config.max_competitive_task_detour_frames)
                if detour <= max_detour:
                    value = task.score * 4 - max(0, detour)
                    if task.score >= 30:
                        value += 40
                    if task.template == "T04":
                        value += 35
                    if me.task_score_base >= self.config.target_task_score:
                        value += 20
                    candidates.append((value, task, approach, detour))
        if not candidates:
            return None
        chosen_value, chosen, chosen_approach, _ = max(candidates, key=lambda item: item[0])
        self._task_approach_nodes[chosen.id] = chosen_approach
        return chosen

    def _task_approach_candidates(self, state: GameState, task: TaskInstance) -> list[str]:
        if task.template != "T04":
            return [task.target]
        candidates = [task.target] + state.neighbors(task.target)
        seen: set[str] = set()
        result: list[str] = []
        for node in candidates:
            if node not in seen:
                seen.add(node)
                result.append(node)
        return result

    def _can_claim_task_from_station(self, state: GameState, task: TaskInstance, station: str | None) -> bool:
        if station is None or not task.available_for(state.player_id):
            return False
        if task.template == "T04":
            return station == task.target or station in state.neighbors(task.target)
        return station == task.target

    def _best_station_resource(self, state: GameState) -> ResourceStock | None:
        stocks = [s for s in state.station_resources(state.me.station)
                  if (s.station, s.resource_type) not in self._rejected_resource_keys
                  and not self._is_object_on_cooldown(state, self._resource_object_key(s.station, s.resource_type))]
        if not stocks:
            return None
        useful = [s for s in stocks if s.resource_type in self.config.resource_priority]
        if self._need_endgame(state) or self._opponent_pressure(state):
            useful = [s for s in useful if s.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE"}]
        if not useful:
            return None
        chosen = max(useful, key=lambda s: self._resource_value(state, s, detour=0))
        return chosen

    def _best_reachable_resource(self, state: GameState, *, exclude_current_station: bool = False) -> ResourceStock | None:
        me = state.me
        if me.station is None:
            return None
        if self._opponent_pressure(state):
            return None
        direct = self._estimate_frames(state, me.station, state.gate_node)
        candidates: list[tuple[int, ResourceStock, int]] = []
        for stock in state.resources:
            if exclude_current_station and stock.station == me.station:
                continue
            if (stock.station, stock.resource_type) in self._rejected_resource_keys or self._is_object_on_cooldown(state, self._resource_object_key(stock.station, stock.resource_type)):
                continue
            if stock.resource_type not in ROUTE_RESOURCE_TYPES:
                continue
            to_res = self._estimate_frames(state, me.station, stock.station)
            to_gate = self._estimate_frames(state, stock.station, state.gate_node)
            detour = to_res + stock.claim_frames + to_gate - direct
            max_detour = self.config.max_resource_detour_frames
            if stock.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE"}:
                max_detour = max(max_detour, self.config.max_valuable_resource_detour_frames)
            if detour <= max_detour:
                candidates.append((self._resource_value(state, stock, detour=detour), stock, detour))
        if not candidates:
            return None
        chosen_value, chosen, _ = max(candidates, key=lambda item: item[0])
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

    # ── Squad ──

    def _scout_action(self, state: GameState, *, after_current_action: bool = False) -> SquadAction | None:
        me = state.me
        if me.squad_available <= 0 or me.station is None or state.phase in RUSH_PHASES:
            return None
        if me.task_score_base >= self.config.target_task_score and not me.verified:
            target = state.gate_node
        else:
            target = None
        if target is None:
            task = self._best_reachable_task(state, exclude_current_station=after_current_action)
            if task is not None:
                target = self._task_approach_nodes.get(task.id, task.target)
        if target is None:
            resource = self._best_reachable_resource(state, exclude_current_station=after_current_action)
            if resource is not None:
                target = resource.station
        if target and target not in self._scout_dispatched and target != me.station:
            self.logger.info("squad_eval", round=state.frame, action="SQUAD_SCOUT", target=target, reason="scout_valuable_target")
            return SquadAction(SquadActionType.SQUAD_SCOUT, target)
        return None

    # ── Movement ──

    def _move_towards_delivery(self, state: GameState, *, squad: SquadAction | None = None) -> ActionBundle:
        return self._move_to(state, state.terminal_node if state.me.verified else state.gate_node, squad=squad)

    def _move_to(self, state: GameState, target: str | None, *, squad: SquadAction | None = None) -> ActionBundle:
        current = state.me.station
        if target is None or current is None:
            return wait("no_target_or_station", active=False)
        if current == target:
            return wait("already_at_target", active=False)

        plan = self.route_planner.plan(state, current, target)
        next_hop = plan.next_station if plan is not None else None
        if next_hop is None:
            next_hop = self._fallback_next_hop(current, target)
            self.logger.info("route_decision", round=state.frame, station=current, target=target, nextHop=next_hop, reason="fallback_route")
        if next_hop is None:
            return wait("no_route", active=False)

        # Obstacle / Guard handling (from root BaselineStrategy)
        station = state.station(next_hop)
        if station is not None and station.has_obstacle:
            # Try T04 for this target
            t04 = self._t04_for_target(state, next_hop)
            if t04 is not None:
                self.logger.info("blocker_decision", target=next_hop, blocker="obstacle", action="CLAIM_TASK", taskId=t04.id)
                return ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, task_id=t04.id), squad=squad)
            # Squad clear
            support = self._squad_blocker_action(state, next_hop, "obstacle") or squad
            if support is not None and support is not squad:
                self.logger.info("blocker_decision", target=next_hop, blocker="obstacle", action="SQUAD_CLEAR")
                return ActionBundle(squad=support)
            if self._should_spend_good_fruit(state):
                self.logger.info("blocker_decision", target=next_hop, blocker="obstacle", action="CLEAR")
                return ActionBundle(main=MainAction(MainActionType.CLEAR, target=next_hop), squad=support)
            self.logger.info("blocker_decision", target=next_hop, blocker="obstacle", action="FORCED_PASS")
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=next_hop), squad=support)

        if station is not None and station.has_enemy_guard(state.me.team_id):
            bad_to_spend = self._bad_fruit_to_break(state, station)
            if bad_to_spend > 0:
                self.logger.info("blocker_decision", target=next_hop, blocker="enemy_guard", action="BREAK_GUARD", badFruit=bad_to_spend)
                return ActionBundle(main=MainAction(MainActionType.BREAK_GUARD, target=next_hop, good_fruit=0, bad_fruit=bad_to_spend), squad=squad)
            support = self._squad_blocker_action(state, next_hop, "enemy_guard") or squad
            if support is not None and support is not squad:
                self.logger.info("blocker_decision", target=next_hop, blocker="enemy_guard", action="SQUAD_WEAKEN")
                return ActionBundle(squad=support)
            if self._should_spend_good_fruit(state):
                self.logger.info("blocker_decision", target=next_hop, blocker="enemy_guard", action="BREAK_GUARD")
                return ActionBundle(main=MainAction(MainActionType.BREAK_GUARD, target=next_hop, good_fruit=1, bad_fruit=0), squad=squad)
            self.logger.info("blocker_decision", target=next_hop, blocker="enemy_guard", action="FORCED_PASS")
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=next_hop), squad=support)

        self.logger.info("move_decision", round=state.frame, station=current, target=next_hop, action="MOVE")
        return ActionBundle(main=MainAction(MainActionType.MOVE, target=next_hop), squad=squad)

    def _should_spend_good_fruit(self, state: GameState) -> bool:
        return self._need_endgame(state) and state.me.good_fruit >= 95

    def _bad_fruit_to_break(self, state: GameState, station) -> int:
        if state.me.bad_fruit <= 0:
            return 0
        needed = max(1, (station.guard_defense + 2) // 3)
        if needed <= min(2, state.me.bad_fruit):
            return needed
        return 0

    def _squad_blocker_action(self, state: GameState, target: str, blocker: str) -> SquadAction | None:
        if state.phase in RUSH_PHASES or state.me.squad_available <= 0:
            return None
        if target in self._scout_dispatched:
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

    def _emergency_move(self, state: GameState) -> ActionBundle:
        target = state.terminal_node if state.me.verified else state.gate_node
        bundle = self._move_to(state, target)
        if bundle.main is None:
            return wait("emergency_heartbeat", active=False)
        return bundle

    def _estimate_frames(self, state: GameState, start: str | None, target: str) -> int:
        value = self.route_planner.estimate_frames(state, start, target)
        if value < 10**8:
            return value
        return self._route_steps_fallback(start, target) * 50

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

    # ── Opportunistic Guard (chokepoint blocking) ──

    def _opportunistic_guard_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if state.phase in RUSH_PHASES or me.status not in PLANNING_STATES or me.station is None:
            return None
        if me.station in {state.start_node, state.gate_node, state.terminal_node}:
            return None
        if me.task_score_base < self.config.target_task_score or me.good_fruit < 88 or self._need_endgame(state):
            return None
        station = state.station(me.station)
        if station is not None and station.has_obstacle:
            return None
        opp_next = self._opponent_next_hop_to_gate(state)
        if opp_next != me.station:
            return None
        if station is not None and station.guard_owner == me.team_id and station.guard_defense > 0:
            if me.squad_available > 0:
                self.logger.info("squad_eval", action="SQUAD_REINFORCE", target=me.station, reason="reinforce_opponent_chokepoint")
                return ActionBundle(squad=SquadAction(SquadActionType.SQUAD_REINFORCE, me.station))
            return None
        if station is not None and station.guard_owner not in (None, "", me.team_id) and station.guard_defense > 0:
            return None
        self.logger.info("blocker_decision", target=me.station, blocker="opponent_route", action="SET_GUARD", reason="chokepoint")
        return ActionBundle(main=MainAction(MainActionType.SET_GUARD, target=me.station, extra_good_fruit=0))

    def _opponent_next_hop_to_gate(self, state: GameState) -> str | None:
        if state.opponent is None or state.opponent.station is None:
            return None
        target = state.terminal_node if state.opponent.verified else state.gate_node
        return self.route_planner.next_hop_to_any(state, state.opponent.station, (target,))

    # ── Station Tracking / Cooldown (from root) ──

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
        self.logger.info("stall_breaker", kind="station", station=station, stayFrames=stay_frames, escapeUntil=until, action="ARM_ESCAPE", reason="station_stall")

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

    def _is_transit_waiting(self, state: GameState) -> bool:
        me = state.me
        if me.status != ConvoyStatus.WAITING:
            return False
        return bool(me.route_edge_id)

    def _log_state_snapshot(self, state: GameState) -> None:
        me = state.me
        gate_cost = self.route_planner.estimate_frames(state, me.station, state.gate_node) if me.station else None
        self.logger.info(
            "state_snapshot",
            round=state.frame, phase=state.phase,
            status=me.status.value, station=me.station,
            verified=me.verified, delivered=me.delivered,
            goodFruit=me.good_fruit, freshness=me.freshness,
            taskScore=me.task_score_base, totalScore=me.total_score,
            resources=me.resources, tasks=len(state.tasks),
            windows=len(state.windows), turnsLeft=state.turns_left,
            gateCost=gate_cost,
        )


BaselineStrategy = FreshnessFirstStrategy
RoadMasterStrategy = FreshnessFirstStrategy
