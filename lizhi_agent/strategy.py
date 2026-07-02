from __future__ import annotations

from dataclasses import dataclass

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

    The design follows common RTS bot architecture: keep protocol and state
    parsing outside the policy, ask a route planner for movement, then choose
    one action by priority.  The first version is intentionally conservative:
    secure delivery, get enough task score for the 90-point participation
    threshold, and only contest resources/tasks when the local value is clear.
    """

    def __init__(self, player_id: str, config: StrategyConfig, logger: DecisionLogger) -> None:
        self.player_id = player_id
        self.config = config
        self.logger = logger
        self.route_planner = RoutePlanner()
        self.window_strategy = WindowStrategy()
        self._start_seen = False
        self._scout_dispatched: set[str] = set()

    def on_start(self, start_data: dict) -> None:
        self._start_seen = True
        self.logger.info(
            "strategy_start",
            nodes=len(start_data.get("nodes", []) or []),
            edges=len(start_data.get("edges", []) or []),
        )

    def decide(self, state: GameState) -> ActionBundle:
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

    def _decide(self, state: GameState) -> Decision:
        me = state.me

        window = state.active_window()
        if window is not None:
            card = self.window_strategy.choose_card(state, window)
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

        if self._need_endgame(state):
            return Decision(self._move_towards_delivery(state), "endgame_delivery")

        fixed_process = self._fixed_process_action(state)
        if fixed_process is not None:
            return Decision(fixed_process, "fixed_process")

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
        action_type = MainActionType.DOCK if station.process_type == "BOARD" else MainActionType.PROCESS
        return ActionBundle(main=MainAction(action_type, target=station.id))

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        tasks = state.station_tasks(state.me.station)
        if not tasks:
            return None
        if state.me.task_score_base >= self.config.greed_task_score and not self._need_endgame(state):
            return None

        def score(task: TaskInstance) -> tuple[int, int]:
            threshold_bonus = 100 if state.me.task_score_base < self.config.target_task_score and task.score >= 30 else 0
            clear_bonus = 20 if task.template == "T04" else 0
            return threshold_bonus + clear_bonus + task.score, -task.process_frames

        best = max(tasks, key=score)
        if state.me.task_score_base < self.config.target_task_score:
            return best
        if best.score >= 30 and not self._need_endgame(state):
            return best
        return None

    def _best_station_resource(self, state: GameState) -> ResourceStock | None:
        stocks = state.station_resources(state.me.station)
        if not stocks:
            return None
        priority = {name: i for i, name in enumerate(self.config.resource_priority)}
        useful = [stock for stock in stocks if stock.resource_type in priority]
        if self._need_endgame(state):
            useful = [stock for stock in useful if stock.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE"}]
        if not useful:
            return None
        return min(useful, key=lambda stock: priority.get(stock.resource_type, 999))

    def _best_reachable_task(self, state: GameState) -> TaskInstance | None:
        if state.me.task_score_base >= self.config.target_task_score:
            return None
        if state.me.station is None:
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        candidates: list[tuple[int, TaskInstance]] = []
        for task in state.tasks:
            if not task.available_for(state.player_id) or task.score <= 0:
                continue
            to_task = self.route_planner.estimate_frames(state, state.me.station, task.target)
            to_gate = self.route_planner.estimate_frames(state, task.target, state.gate_node)
            detour = to_task + task.process_frames + to_gate - direct
            if detour <= self.config.max_task_detour_frames or task.score >= 30:
                value = task.score * 4 - max(0, detour)
                if task.score >= 30:
                    value += 40
                candidates.append((value, task))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _best_reachable_resource(self, state: GameState) -> ResourceStock | None:
        if state.me.station is None:
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        priority = {name: i for i, name in enumerate(self.config.resource_priority)}
        candidates: list[tuple[int, ResourceStock]] = []
        for stock in state.resources:
            if stock.resource_type not in priority:
                continue
            to_res = self.route_planner.estimate_frames(state, state.me.station, stock.station)
            to_gate = self.route_planner.estimate_frames(state, stock.station, state.gate_node)
            detour = to_res + stock.claim_frames + to_gate - direct
            if detour <= self.config.max_resource_detour_frames:
                candidates.append((100 - priority[stock.resource_type] * 10 - max(0, detour), stock))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _squad_scout_action(self, state: GameState) -> SquadAction | None:
        if state.phase in RUSH_PHASES or state.me.squad_available <= 0:
            return None
        if state.me.station is None:
            return None
        for target in self.config.scout_targets:
            if target == state.me.station:
                continue
            if target in self._scout_dispatched or self._has_own_scout_marker(state, target):
                continue
            if self.route_planner.estimate_frames(state, state.me.station, target) < 10**8:
                return SquadAction(SquadActionType.SQUAD_SCOUT, target)
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
        return ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, target=task.target, task_id=task.id))

    def _claim_resource(self, resource: ResourceStock) -> ActionBundle:
        return ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=resource.station, resource_type=resource.resource_type))

    def _move_towards_delivery(self, state: GameState, squad: SquadAction | None = None) -> ActionBundle:
        target = state.terminal_node if state.me.verified else state.gate_node
        return self._move_towards_node(state, target, squad=squad)

    def _move_towards_node(self, state: GameState, target: str, squad: SquadAction | None = None) -> ActionBundle:
        if state.me.station is None:
            return wait("unknown_station", active=False)
        next_hop = self.route_planner.next_hop_to_any(state, state.me.station, (target,))
        if next_hop is None:
            return wait("no_route", active=False)
        return self._move_to(state, next_hop, squad=squad)

    def _move_to(self, state: GameState, target: str, squad: SquadAction | None = None) -> ActionBundle:
        station = state.station(target)
        if station is not None and station.has_obstacle:
            t04 = self._t04_for_target(state, target)
            if t04 is not None:
                return self._claim_task(t04)
            if state.me.good_fruit > 5:
                return ActionBundle(main=MainAction(MainActionType.CLEAR, target=target), squad=squad)
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=squad)
        if station is not None and station.has_enemy_guard(state.me.team_id):
            if state.me.bad_fruit >= 2 or state.me.good_fruit >= 95:
                return ActionBundle(
                    main=MainAction(
                        MainActionType.BREAK_GUARD,
                        target=target,
                        good_fruit=0 if state.me.bad_fruit >= 2 else 1,
                        bad_fruit=min(2, state.me.bad_fruit),
                    ),
                    squad=squad,
                )
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=squad)
        return ActionBundle(main=MainAction(MainActionType.MOVE, target=target), squad=squad)

    def _t04_for_target(self, state: GameState, target: str) -> TaskInstance | None:
        for task in state.tasks:
            if task.template == "T04" and task.target == target and task.available_for(state.player_id):
                return task
        return None
