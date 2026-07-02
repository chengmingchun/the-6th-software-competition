from __future__ import annotations

from dataclasses import dataclass

from lizhi_agent.actions import (
    ActionBundle,
    MainAction,
    MainActionType,
    WindowAction,
    WindowCard,
    wait,
)
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, ResourceStock, TaskInstance, WindowState
from lizhi_agent.route_planner import RoutePlanner


@dataclass(frozen=True)
class Decision:
    bundle: ActionBundle
    reason: str


class WindowStrategy:
    def choose_card(self, state: GameState, window: WindowState) -> WindowCard:
        me = state.me
        # First baseline: use low-regret resources, preserve fruit/freshness.
        if me.guard_points > 0:
            return WindowCard.BING_ZHENG
        if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
            return WindowCard.YAN_DIE
        if me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
            return WindowCard.QIANG_XING
        if me.freshness >= 85 and me.good_fruit >= 90:
            return WindowCard.XIAN_GONG
        return WindowCard.ABSTAIN


class BaselineStrategy:
    def __init__(self, player_id: str, config: StrategyConfig, logger: DecisionLogger) -> None:
        self.player_id = player_id
        self.config = config
        self.logger = logger
        self.route_planner = RoutePlanner()
        self.window_strategy = WindowStrategy()

    def decide(self, state: GameState) -> ActionBundle:
        decision = self._decide(state)
        self.logger.info(
            "decision",
            frame=state.frame,
            station=state.me.station,
            status=state.me.status.value,
            reason=decision.reason,
            action=decision.bundle.to_dict(),
        )
        return decision.bundle

    def _decide(self, state: GameState) -> Decision:
        me = state.me

        window = state.active_window()
        if window is not None:
            card = self.window_strategy.choose_card(state, window)
            return Decision(
                ActionBundle(window=WindowAction(window_id=window.id, card=card), main=MainAction(MainActionType.WAIT)),
                f"window:{window.window_type}:{card.value}",
            )

        if me.delivered or me.status == ConvoyStatus.DELIVERED:
            return Decision(wait("already_delivered"), "already_delivered")

        if me.status in {ConvoyStatus.RESTING, ConvoyStatus.PROCESSING, ConvoyStatus.VERIFYING, ConvoyStatus.CONTESTING, ConvoyStatus.FORCED_PASSING}:
            return Decision(wait(f"busy:{me.status.value}"), f"busy:{me.status.value}")

        if me.station == "S15":
            if me.verified and me.good_fruit > 0 and me.freshness > 0:
                return Decision(ActionBundle(main=MainAction(MainActionType.DELIVER)), "deliver")
            return Decision(wait("at_s15_not_deliverable"), "at_s15_not_deliverable")

        if me.station == "S14" and not me.verified and self._can_verify_gate(state):
            return Decision(ActionBundle(main=MainAction(MainActionType.VERIFY_GATE, target="S14")), "verify_gate")

        if self._need_endgame(state):
            action = self._move_towards_delivery(state)
            return Decision(action, "endgame_delivery")

        fixed_process = self._fixed_process_action(state)
        if fixed_process is not None:
            return Decision(fixed_process, "fixed_process")

        task = self._best_station_task(state)
        if task is not None:
            return Decision(
                ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, target=task.target, task_id=task.id)),
                f"claim_task:{task.template}:{task.id}",
            )

        resource = self._best_station_resource(state)
        if resource is not None:
            return Decision(
                ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=resource.station, resource_type=resource.resource_type)),
                f"claim_resource:{resource.resource_type}",
            )

        action = self._move_towards_delivery(state)
        return Decision(action, "move_towards_delivery")

    def _need_endgame(self, state: GameState) -> bool:
        me = state.me
        if state.phase in {"宫宴冲刺", "BANQUET", "ENDGAME", "FINAL"}:
            return True
        if me.station is None:
            return False
        to_s14 = self.route_planner.estimate_frames(state, me.station, "S14")
        to_s15 = self.route_planner.estimate_frames(state, "S14", "S15")
        verify_frames = 6 if not me.verified else 0
        return state.turns_left <= to_s14 + to_s15 + verify_frames + self.config.endgame_buffer_frames

    def _can_verify_gate(self, state: GameState) -> bool:
        # The official phase gate is: banquet sprint stage. Keep permissive in
        # baseline because field names may not be aligned yet.
        return state.phase in {"宫宴冲刺", "BANQUET", "ENDGAME", "FINAL", "UNKNOWN"}

    def _fixed_process_action(self, state: GameState) -> ActionBundle | None:
        raw = state.raw.get("state", state.raw)
        process_points = raw.get("processPoints") or raw.get("fixedProcesses") or []
        if not isinstance(process_points, list):
            return None
        for item in process_points:
            if not isinstance(item, dict):
                continue
            station = item.get("station") or item.get("stationId") or item.get("node")
            completed = item.get("completed") or item.get("done") or False
            if station == state.me.station and not completed:
                return ActionBundle(main=MainAction(MainActionType.PROCESS, target=str(station)))
        return None

    def _best_station_task(self, state: GameState) -> TaskInstance | None:
        station = state.me.station
        tasks = state.station_tasks(station)
        if not tasks:
            return None

        def task_key(t: TaskInstance) -> tuple[int, int, int]:
            urgent_bonus = 100 if state.me.task_score_base < self.config.target_task_score and t.score >= 30 else 0
            clear_bonus = 30 if t.template == "T04" else 0
            return (urgent_bonus + clear_bonus + t.score, -t.process_frames, 0)

        best = max(tasks, key=task_key)
        if state.me.task_score_base < self.config.target_task_score:
            return best
        # After 90, only do valuable station tasks; avoid late over-greed.
        if best.is_valuable and not self._need_endgame(state):
            return best
        return None

    def _best_station_resource(self, state: GameState) -> ResourceStock | None:
        stocks = state.station_resources(state.me.station)
        if not stocks:
            return None
        priority = {name: i for i, name in enumerate(self.config.resource_priority)}
        useful = [s for s in stocks if s.resource_type in priority]
        if not useful:
            return None
        # Avoid wasting 2 frames on low-value resources in endgame.
        if self._need_endgame(state):
            useful = [s for s in useful if s.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE"}]
        if not useful:
            return None
        return min(useful, key=lambda r: priority.get(r.resource_type, 999))

    def _move_towards_delivery(self, state: GameState) -> ActionBundle:
        me = state.me
        if me.station is None:
            return wait("unknown_station")

        target = "S15" if me.verified else "S14"
        next_hop = self.route_planner.next_hop_to_any(state, me.station, (target,))
        if next_hop is None:
            # If map edges are missing from protocol during early integration,
            # attempt the canonical direct move only when adjacent list contains it.
            neighbors = state.neighbors(me.station)
            if target in neighbors:
                next_hop = target
        if next_hop is None:
            return wait("no_route")
        return ActionBundle(main=MainAction(MainActionType.MOVE, target=next_hop))
