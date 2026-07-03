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
from lizhi_agent.models import ConvoyStatus, GameState, ResourceStock, Station, TaskInstance, WindowState
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
ROUTE_RESOURCE_TYPES = {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL"}
WINDOW_MATRIX = {
    WindowCard.YAN_DIE: {
        WindowCard.YAN_DIE: "DRAW",
        WindowCard.QIANG_XING: "WIN",
        WindowCard.XIAN_GONG: "LOSE",
        WindowCard.BING_ZHENG: "LOSE",
        WindowCard.ABSTAIN: "WIN",
    },
    WindowCard.QIANG_XING: {
        WindowCard.YAN_DIE: "LOSE",
        WindowCard.QIANG_XING: "DRAW",
        WindowCard.XIAN_GONG: "WIN",
        WindowCard.BING_ZHENG: "LOSE",
        WindowCard.ABSTAIN: "WIN",
    },
    WindowCard.XIAN_GONG: {
        WindowCard.YAN_DIE: "WIN",
        WindowCard.QIANG_XING: "LOSE",
        WindowCard.XIAN_GONG: "DRAW",
        WindowCard.BING_ZHENG: "WIN",
        WindowCard.ABSTAIN: "WIN",
    },
    WindowCard.BING_ZHENG: {
        WindowCard.YAN_DIE: "WIN",
        WindowCard.QIANG_XING: "WIN",
        WindowCard.XIAN_GONG: "LOSE",
        WindowCard.BING_ZHENG: "DRAW",
        WindowCard.ABSTAIN: "WIN",
    },
    WindowCard.ABSTAIN: {
        WindowCard.YAN_DIE: "LOSE",
        WindowCard.QIANG_XING: "LOSE",
        WindowCard.XIAN_GONG: "LOSE",
        WindowCard.BING_ZHENG: "LOSE",
        WindowCard.ABSTAIN: "DRAW",
    },
}


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
        high_value = self._is_high_value(state, window)
        value = self._window_value(state, window)
        opponent_card = self._opponent_revealed_card(state, window)
        if opponent_card is not None:
            counter = self._counter_card(state, opponent_card, high_value)
            if counter is not None:
                return WindowChoice(counter, "COUNTER_LAST_CARD", f"counter previous opponent card {opponent_card.value}")
        if self._is_opening_fight(state, window, config):
            options = self._opening_options(state, high_value)
            card, roll = self._weighted_pick(state, window, options)
            return WindowChoice(card, "OPENING_MIX", f"开局窗口混合策略，候选={self._options_text(options)}", roll)
        options = self._ev_options(state, window, high_value, value)
        card, roll = self._weighted_pick(state, window, options)
        style = "WINDOW_EV_MIX" if len(options) > 1 else "WINDOW_EV_FIXED"
        return WindowChoice(card, style, f"value={value};score={self._score_text(state, window)};options={self._options_text(options)}", roll)

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
        if high_value and me.freshness >= 86 and me.good_fruit >= 82:
            options.append((WindowCard.XIAN_GONG, 34 if high_value else 24))
        if high_value and (me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE")):
            options.append((WindowCard.QIANG_XING, 28))
        if high_value and (me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT")):
            options.append((WindowCard.YAN_DIE, 26))
        if not high_value or me.freshness < 75 or me.good_fruit < 70:
            options.append((WindowCard.ABSTAIN, 18))
        if not options:
            options.append((WindowCard.ABSTAIN, 100))
        return options

    def _is_high_value(self, state: GameState, window: WindowState) -> bool:
        return self._window_value(state, window) >= 60

    def _window_value(self, state: GameState, window: WindowState) -> int:
        ctype = str(window.window_type or "").upper()
        resource = str(window.resource_type or "").upper()
        if ctype == "GATE" or window.target == state.gate_node:
            return 95
        if ctype == "PASS":
            return 78
        if ctype == "TASK" or window.task_id:
            task_score = 0
            if window.task_id:
                for task in state.tasks:
                    if task.id == window.task_id:
                        task_score = task.score
                        break
            return 65 + min(45, task_score)
        if resource in {"ICE_BOX", "FAST_HORSE"}:
            return 72
        if resource in {"SHORT_HORSE", "INTEL", "PASS_TOKEN", "OFFICIAL_PERMIT"}:
            return 54
        if ctype in {"OBSTACLE", "DOCK"}:
            return 48
        return 28

    def _affordable_cards(self, state: GameState, high_value: bool) -> list[WindowCard]:
        me = state.me
        cards: list[WindowCard] = []
        if me.guard_points > 0:
            cards.append(WindowCard.BING_ZHENG)
        if high_value and me.freshness >= 82 and me.good_fruit >= 75:
            cards.append(WindowCard.XIAN_GONG)
        if high_value and (me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE")):
            cards.append(WindowCard.QIANG_XING)
        if high_value and (me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT")):
            cards.append(WindowCard.YAN_DIE)
        cards.append(WindowCard.ABSTAIN)
        result: list[WindowCard] = []
        for card in cards:
            if card not in result:
                result.append(card)
        return result

    def _ev_options(self, state: GameState, window: WindowState, high_value: bool, value: int) -> list[tuple[WindowCard, int]]:
        affordable = self._affordable_cards(state, high_value)
        my_score, opp_score = self._score_state(state, window)
        total_rounds = int(window.raw.get("totalRounds") or 3)
        round_index = window.round_index or 1
        remaining_after_this = max(0, total_rounds - round_index)
        if not high_value and value < 45 and my_score <= opp_score:
            active = [card for card in affordable if card != WindowCard.ABSTAIN]
            if not active or (state.me.guard_points <= 1 and state.me.task_score_base < 90):
                return [(WindowCard.ABSTAIN, 100)]
        if my_score > opp_score + remaining_after_this:
            return [(WindowCard.ABSTAIN, 100)]
        if high_value and round_index == 1 and WindowCard.BING_ZHENG in affordable and WindowCard.XIAN_GONG in affordable:
            if str(state.me.team_id or "") == "BLUE":
                return [(WindowCard.XIAN_GONG, 100)]
            return [(WindowCard.BING_ZHENG, 100)]

        opponent_weights = self._opponent_model(state, window, high_value)
        scored: list[tuple[int, WindowCard]] = []
        for card in affordable:
            if card == WindowCard.ABSTAIN and (high_value or my_score <= opp_score):
                continue
            score = self._expected_card_score(card, opponent_weights, value)
            score -= self._card_cost(card, state, high_value)
            if my_score < opp_score:
                score += self._must_win_bonus(card, opponent_weights)
            if my_score > opp_score:
                score += self._avoid_draw_bonus(card, opponent_weights)
            if round_index >= total_rounds:
                score += self._final_round_bonus(card, opponent_weights, my_score, opp_score)
            score += self._role_card_bias(state, card)
            score += self._stable_jitter(state, window, card)
            scored.append((score, card))
        if not scored:
            return [(WindowCard.ABSTAIN, 100)]
        scored.sort(reverse=True)
        best = scored[0][0]
        spread = 18 if high_value else 12
        options: list[tuple[WindowCard, int]] = []
        for score, card in scored:
            if score < best - spread:
                continue
            options.append((card, max(8, score - best + spread + 12)))
        return options or [(scored[0][1], 100)]

    def _opponent_model(self, state: GameState, window: WindowState, high_value: bool) -> dict[WindowCard, int]:
        opponent = state.opponent
        weights = {
            WindowCard.BING_ZHENG: 36,
            WindowCard.XIAN_GONG: 30 if high_value else 14,
            WindowCard.QIANG_XING: 20 if high_value else 8,
            WindowCard.YAN_DIE: 18 if high_value else 7,
            WindowCard.ABSTAIN: 8 if high_value else 24,
        }
        if opponent is not None:
            if opponent.guard_points <= 0:
                weights[WindowCard.BING_ZHENG] = 0
            if opponent.freshness < 80 or opponent.good_fruit < 1:
                weights[WindowCard.XIAN_GONG] = 0
            if not (opponent.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or opponent.has_resource("FAST_HORSE") or opponent.has_resource("SHORT_HORSE")):
                weights[WindowCard.QIANG_XING] = 0
            if not (opponent.has_resource("PASS_TOKEN") or opponent.has_resource("OFFICIAL_PERMIT")):
                weights[WindowCard.YAN_DIE] = 0
        if window.round_index >= 2:
            last = self._opponent_revealed_card(state, window)
            if last is not None:
                weights[last] += 30
        return {card: weight for card, weight in weights.items() if weight > 0}

    def _expected_card_score(self, card: WindowCard, opponent_weights: dict[WindowCard, int], value: int) -> int:
        total = max(1, sum(opponent_weights.values()))
        score = 0
        for opp_card, weight in opponent_weights.items():
            result = WINDOW_MATRIX.get(card, {}).get(opp_card, "DRAW")
            if result == "WIN":
                score += weight * value
            elif result == "LOSE":
                score -= weight * value
            else:
                score -= weight * max(8, value // 5)
        return score // total

    def _card_cost(self, card: WindowCard, state: GameState, high_value: bool) -> int:
        me = state.me
        if card == WindowCard.ABSTAIN:
            return 0
        if card == WindowCard.BING_ZHENG:
            return 10 if high_value else 18
        if card == WindowCard.XIAN_GONG:
            return 12 if me.freshness >= 90 and me.good_fruit >= 90 else 24
        if card == WindowCard.QIANG_XING:
            return 16 if high_value else 28
        if card == WindowCard.YAN_DIE:
            return 14 if high_value else 24
        return 20

    def _must_win_bonus(self, card: WindowCard, opponent_weights: dict[WindowCard, int]) -> int:
        return sum(weight * 2 for opp, weight in opponent_weights.items() if WINDOW_MATRIX.get(card, {}).get(opp) == "WIN")

    def _avoid_draw_bonus(self, card: WindowCard, opponent_weights: dict[WindowCard, int]) -> int:
        return -sum(weight for opp, weight in opponent_weights.items() if WINDOW_MATRIX.get(card, {}).get(opp) == "DRAW")

    def _final_round_bonus(self, card: WindowCard, opponent_weights: dict[WindowCard, int], my_score: int, opp_score: int) -> int:
        if my_score <= opp_score:
            return self._must_win_bonus(card, opponent_weights)
        return self._avoid_draw_bonus(card, opponent_weights)

    def _score_state(self, state: GameState, window: WindowState) -> tuple[int, int]:
        team = str(state.me.team_id or "")
        if team == "RED":
            return window.red_point, window.blue_point
        if team == "BLUE":
            return window.blue_point, window.red_point
        red_pid = str(window.raw.get("redPlayerId") or "")
        blue_pid = str(window.raw.get("bluePlayerId") or "")
        if red_pid == str(state.player_id):
            return window.red_point, window.blue_point
        if blue_pid == str(state.player_id):
            return window.blue_point, window.red_point
        return window.red_point, window.blue_point

    def _score_text(self, state: GameState, window: WindowState) -> str:
        my_score, opp_score = self._score_state(state, window)
        return f"{my_score}:{opp_score}"

    def _stable_jitter(self, state: GameState, window: WindowState, card: WindowCard) -> int:
        seed = "|".join([
            state.player_id,
            str(state.me.team_id or ""),
            str(window.id),
            str(window.target or ""),
            str(window.task_id or ""),
            str(window.resource_type or ""),
            str(window.round_index or 1),
            card.value,
        ])
        return int.from_bytes(hashlib.blake2s(seed.encode("utf-8"), digest_size=2).digest(), "big") % 11

    def _role_card_bias(self, state: GameState, card: WindowCard) -> int:
        team = str(state.me.team_id or "")
        if team == "BLUE":
            if card == WindowCard.XIAN_GONG:
                return 42
            if card == WindowCard.BING_ZHENG:
                return -8
        if card == WindowCard.BING_ZHENG:
            return 42
        if card == WindowCard.XIAN_GONG:
            return -8
        return 0

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
        direct = self._opponent_card_from_direct_fields(state, window)
        if direct is not None:
            return direct
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

    def _opponent_card_from_direct_fields(self, state: GameState, window: WindowState) -> WindowCard | None:
        raw = window.raw
        my_team = str(state.me.team_id or "")
        if not my_team:
            red_pid = str(raw.get("redPlayerId") or "")
            blue_pid = str(raw.get("bluePlayerId") or "")
            if red_pid == str(state.player_id):
                my_team = "RED"
            elif blue_pid == str(state.player_id):
                my_team = "BLUE"
        candidates: list[Any] = []
        if my_team == "RED":
            candidates.extend([raw.get("blueCard"), raw.get("lastBlueCard"), raw.get("blueLastCard")])
        elif my_team == "BLUE":
            candidates.extend([raw.get("redCard"), raw.get("lastRedCard"), raw.get("redLastCard")])
        for key in ("lastOpponentCard", "opponentCard"):
            candidates.append(raw.get(key))
        for value in candidates:
            if isinstance(value, list) and value:
                value = value[-1]
            if value in (None, ""):
                continue
            try:
                return WindowCard(str(value))
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
        self._task_approach_nodes: dict[str, str] = {}
        self._last_attempted_task: tuple[int, str] | None = None
        self._last_attempted_resource: tuple[int, str, str] | None = None
        self._last_attempted_move: tuple[int, str] | None = None
        self._blocked_guard_nodes: set[str] = set()

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
        self._remember_outbound_actions(state, decision.bundle)
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
                gate_intel = self._gate_intel_action(state)
                if gate_intel is not None:
                    return done(gate_intel, "use_intel_before_verify")
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
        if state.phase in RUSH_PHASES:
            rush_resource = self._pre_move_resource_action(state)
            if rush_resource is not None:
                return done(rush_resource, "use_rush_route_resource")
        if self._need_endgame(state) or self._opponent_pressure(state):
            self.logger.info("strategy_step", step="delivery_guard", reason="score_or_deadline_delivery_first")
            scout = self._squad_scout_action(state)
            return done(self._move_towards_delivery(state, squad=scout), "delivery_guard")
        if self._is_station_escape_active(state):
            self.logger.info("stall_breaker", kind="station", station=me.station, stayFrames=self._station_stay_frames(state), escapeUntil=self._station_escape_until.get(me.station or ""), action="MOVE_MAINLINE", reason="当前站点停留过久，暂停本地任务资源，直奔主线")
            scout = self._squad_scout_action(state)
            return done(self._move_towards_delivery(state, squad=scout), "station_stall_escape")
        station_task = self._best_station_task(state)
        if station_task is not None:
            scout = self._squad_scout_action(state, after_current_action=True)
            return done(self._claim_task(station_task, squad=scout), f"claim_task:{station_task.template}:{station_task.id}")
        station_resource = self._best_station_resource(state)
        if station_resource is not None:
            scout = self._squad_scout_action(state, after_current_action=True)
            return done(self._claim_resource(station_resource, squad=scout), f"claim_resource:{station_resource.resource_type}")
        intel_action = self._intel_action(state)
        if intel_action is not None:
            return done(intel_action, "use_intel")
        pre_move_resource = self._pre_move_resource_action(state)
        if pre_move_resource is not None:
            return done(pre_move_resource, "use_route_resource")
        pressure_ice = self._best_reachable_ice_box(state)
        if pressure_ice is not None:
            scout = self._squad_scout_action(state)
            return done(self._move_towards_node(state, pressure_ice.station, squad=scout), "move_to_pressure_ice_box")
        if self._should_lock_delivery(state):
            self.logger.info("strategy_step", step="delivery_guard", reason="score_or_quality_delivery_first")
            scout = self._squad_scout_action(state)
            return done(self._move_towards_delivery(state, squad=scout), "delivery_guard")
        scout = self._squad_scout_action(state)
        route_task = self._best_reachable_task(state)
        if route_task is not None:
            approach = self._task_approach_nodes.get(route_task.id, route_task.target)
            if approach == state.me.station:
                return done(self._claim_task(route_task, squad=scout), f"claim_task:{route_task.template}:{route_task.id}:approach")
            return done(self._move_towards_node(state, approach, squad=scout), f"move_to_task:{route_task.template}:{route_task.id}")
        route_resource = self._best_reachable_resource(state)
        if route_resource is not None:
            return done(self._move_towards_node(state, route_resource.station, squad=scout), f"move_to_resource:{route_resource.resource_type}")
        contest_action = self._opportunistic_guard_action(state)
        if contest_action is not None:
            return done(contest_action, "opportunistic_guard")
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
            if action == "MOVE" and code == "MOVE_BLOCKED_BY_GUARD":
                recent_move = self._recent_attempted_move(state)
                if recent_move is not None:
                    node_id = recent_move
            if action == "CLAIM_TASK" and not task_id:
                task_id = self._recent_attempted_task(state)
            if action == "CLAIM_RESOURCE" and (not node_id or not resource_type):
                recent_resource = self._recent_attempted_resource(state)
                if recent_resource is not None:
                    node_id, resource_type = recent_resource
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
        if action == "MOVE" and code == "MOVE_BLOCKED_BY_GUARD" and node:
            self._blocked_guard_nodes.add(node)
            self.logger.info("feedback_learn", learned="move_blocked_by_guard", nodeId=node, code=code, result=raw)

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

    def _remember_outbound_actions(self, state: GameState, bundle: ActionBundle) -> None:
        if bundle.main is None:
            return
        if bundle.main.action == MainActionType.PROCESS:
            target = bundle.main.target or state.me.station
            if target:
                self._mark_process_pending(str(target), state, "outbound")
        elif bundle.main.action == MainActionType.CLAIM_TASK and bundle.main.task_id:
            self._last_attempted_task = (state.frame, bundle.main.task_id)
        elif bundle.main.action == MainActionType.CLAIM_RESOURCE and bundle.main.target and bundle.main.resource_type:
            self._last_attempted_resource = (state.frame, bundle.main.target, bundle.main.resource_type)
        elif bundle.main.action == MainActionType.MOVE and bundle.main.target:
            self._last_attempted_move = (state.frame, bundle.main.target)
        elif bundle.main.action == MainActionType.USE_RESOURCE and bundle.main.resource_type == "INTEL" and bundle.main.target:
            self._scout_dispatched.add(bundle.main.target)

    def _recent_attempted_task(self, state: GameState) -> str | None:
        if self._last_attempted_task is None:
            return None
        frame, task_id = self._last_attempted_task
        if state.frame - frame <= 3:
            return task_id
        return None

    def _recent_attempted_resource(self, state: GameState) -> tuple[str, str] | None:
        if self._last_attempted_resource is None:
            return None
        frame, node_id, resource_type = self._last_attempted_resource
        if state.frame - frame <= 3:
            return node_id, resource_type
        return None

    def _recent_attempted_move(self, state: GameState) -> str | None:
        if self._last_attempted_move is None:
            return None
        frame, node_id = self._last_attempted_move
        if state.frame - frame <= 3:
            return node_id
        return None

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
            self.logger.info(
                "process_pending_unconfirmed",
                station=station,
                startedAt=started_at,
                frame=state.frame,
                graceFrames=self.config.process_start_grace_frames,
                reason="PROCESS 已提交但服务端未显示处理中，解锁重试，避免南岭驿原地空等",
            )
            self._pending_process_until.pop(station, None)
            self._pending_process_started_at.pop(station, None)
            return None
        if state.frame <= until:
            wait_reason = (
                "服务端已显示处理中，等待 PROCESS_COMPLETE，不重复提交，不移动离站"
                if server_confirms_processing
                else "PROCESS 刚提交，短暂等待服务端进入处理中"
            )
            self.logger.info("process_pending_wait", station=station, startedAt=started_at, until=until, confirmed=server_confirms_processing, reason=wait_reason)
            return wait("pending_process", active=False)
        self.logger.info("process_pending_timeout", station=station, startedAt=started_at, until=until, reason="等待超时，允许重新提交 PROCESS")
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
        target = state.terminal_node if me.verified else state.gate_node
        remaining_cost = self.route_planner.estimate_frames(state, me.station, target)
        has_speed_resource = me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE")
        if not me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") and not has_speed_resource and me.good_fruit >= 88 and me.freshness >= 88:
            if remaining_cost >= 8 and state.turns_left <= remaining_cost + 32:
                self.logger.info("rush_tactic", action="RUSH_SPEED", reason="deadline_speedup", remainingCost=remaining_cost, turnsLeft=state.turns_left)
                return ActionBundle(main=MainAction(MainActionType.RUSH_SPEED))
        if has_speed_resource and not me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") and me.freshness >= 90 and remaining_cost >= 6:
            self.logger.info("rush_tactic_skip", action="RUSH_PROTECT", reason="use_horse_before_protect", remainingCost=remaining_cost)
            return None
        if not me.has_buff("RUSH_PROTECT") and (me.task_score_base >= self.config.target_task_score or me.freshness <= 90):
            self.logger.info("rush_tactic", action="RUSH_PROTECT", reason="protect_freshness_in_rush", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.RUSH_PROTECT))
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
        if me.freshness <= 88 and me.task_score_base >= self.config.target_task_score:
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="protect_target_score_quality", freshness=me.freshness, taskScore=me.task_score_base, turnsLeft=state.turns_left)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.freshness <= 92 and me.task_score_base >= self.config.target_task_score and (self._weather_forecast(state, "HOT") or state.turns_left < 260):
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="preempt_hot_or_late_freshness_loss", freshness=me.freshness, taskScore=me.task_score_base, turnsLeft=state.turns_left)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.freshness <= 90 and (state.phase in RUSH_PHASES or self._should_lock_delivery(state) or self._hot_weather_active(state)):
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="protect_quality_in_pressure_or_hot", freshness=me.freshness, taskScore=me.task_score_base, phase=state.phase, weather=state.weather.active_types if state.weather else ())
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        if me.freshness <= 90 and me.task_score_base >= self.config.competitive_task_score:
            self.logger.info("resource_use", resourceType="ICE_BOX", reason="protect_high_score_quality", freshness=me.freshness, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX"))
        return None

    def _hot_weather_active(self, state: GameState) -> bool:
        return bool(state.weather and "HOT" in state.weather.active_types)

    def _weather_forecast(self, state: GameState, weather_type: str) -> bool:
        return bool(state.weather and weather_type in state.weather.forecast_types)

    def _pre_move_resource_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.status not in PLANNING_STATES or me.station is None:
            return None
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        target = self._current_route_objective(state)
        remaining_cost = self.route_planner.estimate_frames(state, me.station, target)
        if remaining_cost >= 5 and me.has_resource("FAST_HORSE"):
            self.logger.info("resource_use", resourceType="FAST_HORSE", reason="pre_move_long_route", target=target, remainingCost=remaining_cost)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if remaining_cost >= 3 and me.has_resource("SHORT_HORSE") and (me.task_score_base >= 60 or state.turns_left < 420):
            self.logger.info("resource_use", resourceType="SHORT_HORSE", reason="pre_move_medium_route", target=target, remainingCost=remaining_cost)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _intel_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("INTEL") or me.status not in PLANNING_STATES or me.station is None:
            return None
        target = self._intel_target(state)
        if me.squad_available > 0 and state.phase not in RUSH_PHASES and target != state.gate_node:
            return None
        if target is None:
            self.logger.info("resource_use_skip", resourceType="INTEL", reason="no_route_scout_target")
            return None
        self.logger.info("resource_use", resourceType="INTEL", reason="route_intel_scout", target=target)
        return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, target=target, resource_type="INTEL"))

    def _intel_target(self, state: GameState) -> str | None:
        forbidden = self._scout_forbidden(state)
        objective = self._current_route_objective(state)
        target, _ = self._priority_scout_target(state, objective, forbidden)
        return target

    def _gate_intel_action(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if not me.has_resource("INTEL") or me.station != state.gate_node or me.verified:
            return None
        if state.phase not in RUSH_PHASES:
            return None
        if state.gate_node in self._scout_dispatched:
            return None
        if self._has_own_scout_marker(state, state.gate_node):
            return None
        self.logger.info("resource_use", resourceType="INTEL", reason="gate_verify_scout_marker", target=state.gate_node)
        return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, target=state.gate_node, resource_type="INTEL"))

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
        if self._freshness_pressure(state) >= 2 and state.me.task_score_base >= self.config.target_task_score:
            self.logger.info("task_eval_station", station=state.me.station, candidates=[], reason="freshness_pressure_delivery_first")
            return None
        tasks = [
            task
            for task in state.tasks
            if self._can_claim_task_from_station(state, task, state.me.station)
            and task.id not in self._rejected_task_ids
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
        self.logger.info("task_eval_station", station=state.me.station, candidates=[{"taskId": t.id, "template": t.template, "score": t.score, "processFrames": t.process_frames, "rank": score(t)} for t in tasks], chosen=best.id)
        if self._freshness_pressure(state) >= 2 and best.score < 45:
            return None
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
        if self._freshness_pressure(state) >= 2:
            ice = [stock for stock in useful if stock.resource_type == "ICE_BOX"]
            if ice:
                useful = ice
        if not useful:
            self.logger.info("resource_eval_station", station=state.me.station, candidates=[{"resourceType": s.resource_type, "amount": s.amount} for s in stocks], chosen=None)
            return None
        chosen = max(useful, key=lambda stock: self._resource_value(state, stock, detour=0))
        self.logger.info("resource_eval_station", station=state.me.station, candidates=[{"resourceType": s.resource_type, "amount": s.amount, "value": self._resource_value(state, s, detour=0)} for s in stocks], chosen=chosen.resource_type)
        return chosen

    def _best_reachable_task(self, state: GameState, *, exclude_current_station: bool = False) -> TaskInstance | None:
        if state.me.task_score_base >= self.config.greed_task_score or state.me.station is None:
            return None
        freshness_pressure = self._freshness_pressure(state)
        if freshness_pressure >= 3 and state.me.task_score_base >= self.config.target_task_score:
            self.logger.info("task_eval_reachable", candidates=[], reason="critical_freshness_delivery_first")
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        candidates: list[tuple[int, TaskInstance, str, int, int, int]] = []
        for task in state.tasks:
            if task.id in self._rejected_task_ids or self._is_object_on_cooldown(state, self._task_object_key(task.id)):
                continue
            if not task.available_for(state.player_id) or task.score <= 0:
                continue
            for approach in self._task_approach_candidates(state, task):
                if exclude_current_station and approach == state.me.station:
                    continue
                to_task = self.route_planner.estimate_frames(state, state.me.station, approach)
                to_gate = self.route_planner.estimate_frames(state, approach, state.gate_node)
                detour = to_task + task.process_frames + to_gate - direct
                max_detour = self.config.max_task_detour_frames + (12 if task.score >= 30 else 0)
                if task.template == "T04" and task.score >= 30:
                    max_detour += 18
                if state.me.task_score_base >= self.config.target_task_score and task.score >= 30:
                    max_detour = max(max_detour, self.config.max_competitive_task_detour_frames)
                if freshness_pressure >= 2:
                    max_detour = min(max_detour, 10 if state.me.task_score_base >= self.config.target_task_score else 16)
                elif freshness_pressure == 1:
                    max_detour = min(max_detour, 20 if state.me.task_score_base >= self.config.target_task_score else max_detour)
                if detour <= max_detour:
                    value = task.score * 4 - max(0, detour)
                    if task.score >= 30:
                        value += 40
                    if task.template == "T04":
                        value += 35
                    if state.me.task_score_base >= self.config.target_task_score:
                        value += 20
                    if freshness_pressure >= 2:
                        value -= max(0, detour) * 4 + task.process_frames * 3
                    candidates.append((value, task, approach, detour, to_task, to_gate))
        if not candidates:
            self.logger.info("task_eval_reachable", directToGate=direct, candidates=[])
            return None
        chosen_value, chosen, chosen_approach, chosen_detour, chosen_to_task, chosen_to_gate = max(candidates, key=lambda item: item[0])
        self._task_approach_nodes[chosen.id] = chosen_approach
        self.logger.info("task_eval_reachable", directToGate=direct, candidates=[{"taskId": t.id, "template": t.template, "target": t.target, "approach": a, "score": t.score, "value": v, "detour": d, "toTask": tt, "toGate": tg} for v, t, a, d, tt, tg in sorted(candidates, key=lambda item: item[0], reverse=True)[:5]], chosen=chosen.id, chosenApproach=chosen_approach, chosenValue=chosen_value, chosenDetour=chosen_detour, chosenToTask=chosen_to_task, chosenToGate=chosen_to_gate)
        return chosen

    def _can_claim_task_from_station(self, state: GameState, task: TaskInstance, station: str | None) -> bool:
        if station is None or not task.available_for(state.player_id):
            return False
        if task.template == "T04":
            return station == task.target or station in state.neighbors(task.target)
        return station == task.target

    def _task_approach_candidates(self, state: GameState, task: TaskInstance) -> list[str]:
        if task.template != "T04":
            return [task.target]
        candidates = [task.target, *state.neighbors(task.target)]
        seen: set[str] = set()
        result: list[str] = []
        for node in candidates:
            if node not in seen:
                seen.add(node)
                result.append(node)
        return result

    def _best_reachable_resource(self, state: GameState, *, exclude_current_station: bool = False) -> ResourceStock | None:
        if state.me.station is None:
            return None
        if self._opponent_pressure(state):
            self.logger.info("resource_eval_reachable", candidates=[], reason="opponent_pressure")
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        candidates: list[tuple[int, ResourceStock, int]] = []
        for stock in state.resources:
            if exclude_current_station and stock.station == state.me.station:
                continue
            if (stock.station, stock.resource_type) in self._rejected_resource_keys or self._is_object_on_cooldown(state, self._resource_object_key(stock.station, stock.resource_type)):
                continue
            if stock.resource_type not in ROUTE_RESOURCE_TYPES:
                continue
            to_res = self.route_planner.estimate_frames(state, state.me.station, stock.station)
            to_gate = self.route_planner.estimate_frames(state, stock.station, state.gate_node)
            detour = to_res + stock.claim_frames + to_gate - direct
            max_detour = self.config.max_resource_detour_frames
            if stock.resource_type in {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE"}:
                max_detour = max(max_detour, self.config.max_valuable_resource_detour_frames)
            if stock.resource_type == "ICE_BOX" and self._freshness_pressure(state) >= 1:
                max_detour = max(max_detour, 72)
            elif self._freshness_pressure(state) >= 2 and stock.resource_type not in {"ICE_BOX", "FAST_HORSE"}:
                max_detour = min(max_detour, 4)
            if detour <= max_detour:
                candidates.append((self._resource_value(state, stock, detour=detour), stock, detour))
        if not candidates:
            self.logger.info("resource_eval_reachable", directToGate=direct, candidates=[])
            return None
        chosen_value, chosen, chosen_detour = max(candidates, key=lambda item: item[0])
        self.logger.info("resource_eval_reachable", directToGate=direct, candidates=[{"resourceType": s.resource_type, "station": s.station, "value": v, "detour": d} for v, s, d in sorted(candidates, key=lambda item: item[0], reverse=True)[:5]], chosen=chosen.resource_type, chosenStation=chosen.station, chosenValue=chosen_value, chosenDetour=chosen_detour)
        return chosen

    def _best_reachable_ice_box(self, state: GameState) -> ResourceStock | None:
        if state.me.station is None or not self._should_lock_delivery(state):
            return None
        direct = self.route_planner.estimate_frames(state, state.me.station, state.gate_node)
        candidates: list[tuple[int, ResourceStock, int]] = []
        for stock in state.resources:
            if stock.resource_type != "ICE_BOX":
                continue
            if (stock.station, stock.resource_type) in self._rejected_resource_keys or self._is_object_on_cooldown(state, self._resource_object_key(stock.station, stock.resource_type)):
                continue
            to_res = self.route_planner.estimate_frames(state, state.me.station, stock.station)
            to_gate = self.route_planner.estimate_frames(state, stock.station, state.gate_node)
            detour = to_res + stock.claim_frames + to_gate - direct
            if detour <= 24:
                candidates.append((self._resource_value(state, stock, detour=detour), stock, detour))
        if not candidates:
            self.logger.info("resource_eval_pressure_ice", directToGate=direct, candidates=[])
            return None
        chosen_value, chosen, chosen_detour = max(candidates, key=lambda item: item[0])
        self.logger.info("resource_eval_pressure_ice", directToGate=direct, candidates=[{"station": s.station, "value": v, "detour": d} for v, s, d in candidates], chosen=chosen.station, chosenValue=chosen_value, chosenDetour=chosen_detour)
        return chosen

    def _resource_value(self, state: GameState, stock: ResourceStock, detour: int) -> int:
        priority = {name: i for i, name in enumerate(self.config.resource_priority)}
        base = 100 - priority.get(stock.resource_type, 999) * 8
        me = state.me
        if stock.resource_type == "ICE_BOX":
            base += 75 if me.freshness <= 82 else (55 if me.freshness <= 90 else 28)
            if me.task_score_base >= self.config.target_task_score:
                base += 40
            if self._hot_weather_active(state) or self._weather_forecast(state, "HOT"):
                base += 22
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

    def _squad_scout_action(self, state: GameState, *, after_current_action: bool = False) -> SquadAction | None:
        if state.phase in RUSH_PHASES or state.me.squad_available <= 0 or state.me.station is None:
            return None
        forbidden = self._scout_forbidden(state)
        objective = self._scout_objective(state, exclude_current_station=after_current_action)
        target, candidates = self._priority_scout_target(state, objective, forbidden)
        if target is not None:
            self.logger.info("squad_eval", action="SQUAD_SCOUT", target=target, reason="valuable_route_scout", objective=objective, candidates=candidates)
            return SquadAction(SquadActionType.SQUAD_SCOUT, target)
        self.logger.info("squad_eval", action=None, reason="no_valuable_route_scout_target", objective=objective, candidates=candidates)
        return None

    def _scout_forbidden(self, state: GameState) -> set[str]:
        return {state.me.station or "", state.start_node, state.terminal_node, *map(str, state.roles.get("safeZoneNodeIds", []) or [])}

    def _scout_objective(self, state: GameState, *, exclude_current_station: bool = False) -> str:
        me = state.me
        if self._need_endgame(state) or self._opponent_pressure(state) or self._should_lock_delivery(state):
            return state.terminal_node if me.verified else state.gate_node
        route_task = self._best_reachable_task(state, exclude_current_station=exclude_current_station)
        if route_task is not None:
            return self._task_approach_nodes.get(route_task.id, route_task.target)
        route_resource = self._best_reachable_resource(state, exclude_current_station=exclude_current_station)
        if route_resource is not None:
            return route_resource.station
        return state.terminal_node if me.verified else state.gate_node

    def _current_route_objective(self, state: GameState) -> str:
        if self._need_endgame(state) or self._opponent_pressure(state) or self._should_lock_delivery(state):
            return state.terminal_node if state.me.verified else state.gate_node
        route_task = self._best_reachable_task(state)
        if route_task is not None:
            return self._task_approach_nodes.get(route_task.id, route_task.target)
        route_resource = self._best_reachable_resource(state)
        if route_resource is not None:
            return route_resource.station
        return state.terminal_node if state.me.verified else state.gate_node

    def _should_lock_delivery(self, state: GameState) -> bool:
        me = state.me
        if me.task_score_base >= self.config.greed_task_score:
            return True
        if me.task_score_base >= self.config.target_task_score and me.freshness < 88:
            return True
        if me.task_score_base >= self.config.competitive_task_score and me.freshness < 90:
            return True
        if me.good_fruit < 78 or me.freshness < 68:
            return me.task_score_base >= self.config.target_task_score
        return False

    def _freshness_pressure(self, state: GameState) -> int:
        me = state.me
        pressure = 0
        if me.freshness < 92 and me.task_score_base >= self.config.competitive_task_score:
            pressure += 1
        if me.freshness < 86 and me.task_score_base >= self.config.target_task_score:
            pressure += 1
        if me.freshness < 78:
            pressure += 1
        if self._hot_weather_active(state) or self._weather_forecast(state, "HOT"):
            pressure += 1
        if state.phase in RUSH_PHASES:
            pressure += 1
        return pressure

    def _should_prepare_gate_scout(self, state: GameState) -> bool:
        me = state.me
        if me.verified or me.station in {None, state.gate_node}:
            return False
        gate_cost = self.route_planner.estimate_frames(state, me.station, state.gate_node)
        if gate_cost >= 10**8:
            return False
        if state.phase in RUSH_PHASES or self._need_endgame(state) or self._opponent_pressure(state) or self._should_lock_delivery(state):
            return True
        if state.frame >= 240 and me.task_score_base >= self.config.target_task_score and (gate_cost <= 16 or state.turns_left < 360):
            return True
        if state.frame >= 240 and me.task_score_base >= self.config.target_task_score // 2 and me.freshness <= 86 and gate_cost <= 12:
            return True
        return False

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
        if self._is_my_mainline_next_hop(state, me.station) and not self._is_key_chokepoint(me.station):
            return None
        if self._opponent_next_hop_to_gate(state) != me.station:
            return None
        if station is not None and station.guard_owner == me.team_id and station.guard_defense > 0:
            if me.squad_available > 0:
                self.logger.info("squad_eval", action="SQUAD_REINFORCE", target=me.station, reason="reinforce_opponent_chokepoint")
                return ActionBundle(squad=SquadAction(SquadActionType.SQUAD_REINFORCE, me.station))
            return None
        if station is not None and station.guard_owner not in (None, "", me.team_id) and station.guard_defense > 0:
            return None
        self.logger.info("blocker_decision", target=me.station, blocker="opponent_route", action="SET_GUARD", reason="zero_good_fruit_chokepoint")
        return ActionBundle(main=MainAction(MainActionType.SET_GUARD, target=me.station, extra_good_fruit=0))

    def _is_key_chokepoint(self, station: str) -> bool:
        return station in {"S09", "S10", "S11", "S13", "S14"}

    def _is_my_mainline_next_hop(self, state: GameState, station: str) -> bool:
        target = state.terminal_node if state.me.verified else state.gate_node
        return self.route_planner.next_hop_to_any(state, state.me.station, (target,)) == station

    def _opponent_next_hop_to_gate(self, state: GameState) -> str | None:
        if state.opponent is None or state.opponent.station is None:
            return None
        if state.opponent.verified:
            target = state.terminal_node
        else:
            target = state.gate_node
        return self.route_planner.next_hop_to_any(state, state.opponent.station, (target,))

    def _priority_scout_target(self, state: GameState, objective: str, forbidden: set[str]) -> tuple[str | None, list[dict[str, int | str]]]:
        plan = self.route_planner.plan(state, state.me.station, objective)
        if plan is None:
            return None, []
        scored: list[tuple[int, int, str, str]] = []
        details: list[dict[str, int | str]] = []
        for index, node in enumerate(plan.path[1 : 1 + SCOUT_PATH_LOOKAHEAD], start=1):
            if node in forbidden or node in self._scout_dispatched or self._has_own_scout_marker(state, node):
                continue
            eta = self.route_planner.estimate_frames(state, state.me.station, node)
            if eta > 38:
                details.append({"target": node, "value": 0, "reason": "eta_too_late_for_marker", "hop": index})
                continue
            score, reason = self._scout_target_value(state, node, objective)
            details.append({"target": node, "value": score, "reason": reason, "hop": index, "eta": eta})
            if score <= 0:
                continue
            scored.append((score, -index, node, reason))
        if not scored:
            return None, details
        _, _, target, _ = max(scored)
        return target, details

    def _scout_target_value(self, state: GameState, node: str, objective: str) -> tuple[int, str]:
        station = state.station(node)
        reasons: list[str] = []
        value = 0
        task_score = sum(task.score for task in state.tasks if task.target == node and task.available_for(state.player_id) and task.id not in self._rejected_task_ids)
        if task_score > 0:
            value += 70 + min(60, task_score)
            reasons.append("task")
        resource_values = [self._resource_value(state, stock, detour=0) for stock in state.resources if stock.station == node and stock.amount > 0 and stock.resource_type in ROUTE_RESOURCE_TYPES]
        if resource_values:
            value += 45 + min(50, max(resource_values))
            reasons.append("resource")
        if station is not None and station.process_type and station.process_round > 0 and station.process_type != "VERIFY":
            value += 35 + min(20, station.process_round)
            reasons.append("process")
        if node == state.gate_node and self._should_prepare_gate_scout(state):
            value += 95
            reasons.append("gate_verify")
        if station is not None and station.has_obstacle:
            value += 28
            reasons.append("obstacle")
        if station is not None and station.has_enemy_guard(state.me.team_id):
            value += 28 + station.guard_defense * 4
            reasons.append("enemy_guard")
        if node == objective and reasons:
            value += 20
        return value, "+".join(reasons) if reasons else "pass_through"

    def _has_own_scout_marker(self, state: GameState, target: str) -> bool:
        station = state.station(target)
        if station is None:
            return False
        markers = station.raw.get("scouted")
        if not isinstance(markers, list):
            return False
        return any(isinstance(marker, dict) and marker.get("teamId") == state.me.team_id and marker.get("remainingTriggers", 1) for marker in markers)

    def _claim_task(self, task: TaskInstance, squad: SquadAction | None = None) -> ActionBundle:
        return ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, task_id=task.id), squad=squad)

    def _claim_resource(self, resource: ResourceStock, squad: SquadAction | None = None) -> ActionBundle:
        return ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=resource.station, resource_type=resource.resource_type), squad=squad)

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
            if self._should_spend_good_fruit_to_clear(state):
                self.logger.info("blocker_decision", target=target, blocker="obstacle", action="CLEAR", reason="deadline_over_good_fruit")
                return ActionBundle(main=MainAction(MainActionType.CLEAR, target=target), squad=support)
            self.logger.info("blocker_decision", target=target, blocker="obstacle", action="FORCED_PASS", reason="save_good_fruit")
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=support)
        if self._has_enemy_guard_or_learned_block(state, target, station):
            bad_to_spend = self._bad_fruit_to_break_guard(state, station)
            if bad_to_spend > 0:
                self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="BREAK_GUARD", reason="spend_bad_fruit_first", badFruit=bad_to_spend)
                return ActionBundle(main=MainAction(MainActionType.BREAK_GUARD, target=target, good_fruit=0, bad_fruit=bad_to_spend), squad=squad)
            support = self._squad_blocker_action(state, target, "enemy_guard") or squad
            if support is not None and support is not squad:
                self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="SQUAD_WEAKEN", reason="save_good_fruit")
                return ActionBundle(squad=support)
            if self._should_spend_good_fruit_to_break_guard(state, station):
                self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="BREAK_GUARD", reason="deadline_over_good_fruit")
                return ActionBundle(main=MainAction(MainActionType.BREAK_GUARD, target=target, good_fruit=1, bad_fruit=0), squad=squad)
            self.logger.info("blocker_decision", target=target, blocker="enemy_guard", action="FORCED_PASS", reason="save_good_fruit")
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=support)
        self.logger.info("move_decision", target=target, action="MOVE")
        return ActionBundle(main=MainAction(MainActionType.MOVE, target=target), squad=squad)

    def _has_enemy_guard_or_learned_block(self, state: GameState, target: str, station: Station | None) -> bool:
        if station is not None and station.has_enemy_guard(state.me.team_id):
            return True
        return target in self._blocked_guard_nodes

    def _should_spend_good_fruit_to_clear(self, state: GameState) -> bool:
        return self._need_endgame(state) and state.me.good_fruit >= 95

    def _bad_fruit_to_break_guard(self, state: GameState, station: Station | None) -> int:
        if state.me.bad_fruit <= 0:
            return 0
        defense = max(1, station.guard_defense if station is not None else 2)
        needed = max(1, (defense + 2) // 3)
        if needed <= min(2, state.me.bad_fruit):
            return needed
        return 0

    def _should_spend_good_fruit_to_break_guard(self, state: GameState, station: Station | None) -> bool:
        defense = max(1, station.guard_defense if station is not None else 2)
        return self._need_endgame(state) and state.me.good_fruit >= 98 and defense <= 2

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
