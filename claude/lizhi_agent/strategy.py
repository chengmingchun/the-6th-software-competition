from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from lizhi_agent.actions import (
    ActionBundle, MainAction, MainActionType,
    SquadAction, SquadActionType, WindowAction, WindowCard, wait,
)
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, ResourceStock, Station, TaskInstance, WindowState
from lizhi_agent.route_planner import RoutePlanner


BUSY_STATES = {ConvoyStatus.PROCESSING, ConvoyStatus.VERIFYING, ConvoyStatus.RESTING,
               ConvoyStatus.FORCED_PASSING, ConvoyStatus.CONTESTING}
MOVING_STATES = {ConvoyStatus.MOVING}
RUSH_PHASES = {"RUSH", "BANQUET", "ENDGAME", "FINAL", "宫宴冲刺"}
PLANNING_STATES = {ConvoyStatus.IDLE, ConvoyStatus.WAITING, ConvoyStatus.UNKNOWN, ConvoyStatus.COST_BANKRUPT}
PROCESS_RETRY_CODES = {"PROCESS_REQUIRED", "PROCESS_INTERRUPTED", "INTERRUPTED"}
PROCESS_HARD_REJECT_CODES = {"PROCESS_NOT_AVAILABLE", "NOT_AT_TARGET_NODE", "INVALID_TARGET"}
WINDOW_REJECT_CODES = {"WINDOW_NOT_ACTIVE", "WINDOW_NOT_AVAILABLE", "WINDOW_NOT_YOUR_TURN",
                       "WINDOW_CARD_INVALID", "WINDOW_DRAW_RETRY_LIMIT", "CONTEST_NOT_ACTIVE",
                       "CONTEST_NOT_FOUND", "INVALID_CONTEST", "INVALID_ACTION"}
WINDOW_TERMINAL_STATUSES = {"SUPPRESSED", "RESOLVED", "FINISHED", "FINISH", "ENDED", "END",
                            "CLOSED", "COMPLETED", "COMPLETE", "SETTLED"}
WINDOW_HARD_MAX_SENDS = 3
SCOUT_PATH_LOOKAHEAD = 3
ROUTE_RESOURCE_TYPES = {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL"}

# The optimal road route for maximum task density
ROAD_PATH = ["S01", "S02", "S03", "S07", "S09", "S10", "S11", "S12", "S13", "S14", "S15"]
ROAD_STATION_ORDER = {s: i for i, s in enumerate(ROAD_PATH)}

# Direct mainline stations (skip detours)
MAINLINE_STATIONS = set(ROAD_PATH)

# Stations with valuable resources on the road
ROAD_RESOURCE_STATIONS = {"S03": {"ICE_BOX", "PASS_TOKEN", "INTEL"},
                          "S07": {"ICE_BOX", "SHORT_HORSE"},
                          "S09": {"FAST_HORSE", "OFFICIAL_PERMIT"},
                          "S10": {"INTEL"},
                          "S11": {"INTEL"}}

# Fixed process stations on road
ROAD_PROCESS_STATIONS = {"S02": 4, "S11": 5, "S13": 5}

# Chokepoints for guards
CHOKEPOINT_STATIONS = {"S10", "S11"}


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
    def choose(self, state: GameState, window: WindowState, config: StrategyConfig) -> WindowChoice:
        me = state.me
        high_value = window.window_type in {"GATE", "TASK", "PASS"} or window.resource_type in {"FAST_HORSE", "ICE_BOX"}
        opp_card = self._opponent_revealed_card(state, window)
        if opp_card is not None:
            counter = self._counter_card(state, opp_card, high_value)
            if counter is not None:
                return WindowChoice(counter, "COUNTER", f"counter {opp_card.value}")
        if self._is_opening_fight(state, window, config):
            options = self._opening_options(state, high_value)
            card, roll = self._weighted_pick(state, window, options)
            return WindowChoice(card, "MIX", self._options_text(options), roll)
        if high_value:
            if me.guard_points > 0:
                return WindowChoice(WindowCard.BING_ZHENG, "BZ", "high_value+guard")
            if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
                return WindowChoice(WindowCard.YAN_DIE, "YD", "high_value+doc")
            if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
                return WindowChoice(WindowCard.QIANG_XING, "QX", "high_value+speed")
            if me.freshness >= 82 and me.good_fruit >= 76:
                return WindowChoice(WindowCard.XIAN_GONG, "XG", "high_value+fruit")
        if me.guard_points >= 2:
            return WindowChoice(WindowCard.BING_ZHENG, "BZ_SPARE", "spare_guard")
        if me.freshness >= 80 and me.good_fruit >= 70:
            return WindowChoice(WindowCard.XIAN_GONG, "XG_CHEAP", "cheap")
        return WindowChoice(WindowCard.ABSTAIN, "SAVE", "save")

    def choose_card(self, state: GameState, window: WindowState) -> WindowCard:
        return self.choose(state, window, StrategyConfig.default()).card

    def _is_opening_fight(self, state, window, config):
        if state.frame > 120:
            return False
        target = window.target or state.me.station
        if target in {None, state.start_node, state.gate_node, state.terminal_node}:
            return False
        return window.window_type in {"TASK", "RESOURCE", "PASS", "UNKNOWN"} or window.resource_type is not None

    def _opening_options(self, state, high_value):
        me = state.me
        opts = []
        if me.guard_points > 0:
            opts.append((WindowCard.BING_ZHENG, 42 if high_value else 30))
        if high_value and me.freshness >= 86 and me.good_fruit >= 82:
            opts.append((WindowCard.XIAN_GONG, 34 if high_value else 24))
        if high_value and (me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE")):
            opts.append((WindowCard.QIANG_XING, 28))
        if high_value and (me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT")):
            opts.append((WindowCard.YAN_DIE, 26))
        if not high_value or me.freshness < 75 or me.good_fruit < 70:
            opts.append((WindowCard.ABSTAIN, 18))
        if not opts:
            opts.append((WindowCard.ABSTAIN, 100))
        return opts

    def _weighted_pick(self, state, window, options):
        total = sum(w for _, w in options)
        seed = "|".join([state.player_id, str(window.id), str(window.target or state.me.station),
                         str(window.task_id or ""), str(window.resource_type or ""),
                         str(window.round_index), str(state.frame // 3)])
        roll = int.from_bytes(hashlib.blake2s(seed.encode(), digest_size=4).digest(), "big") % total
        c = 0
        for card, w in options:
            c += w
            if roll < c:
                return card, roll
        return options[-1][0], roll

    def _options_text(self, options):
        return ",".join(f"{c.value}:{w}" for c, w in options)

    def _opponent_revealed_card(self, state, window):
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

    def _counter_card(self, state, opp, high_value):
        me = state.me
        if opp == WindowCard.YAN_DIE:
            if me.guard_points > 0:
                return WindowCard.BING_ZHENG
            if high_value and me.freshness >= 82 and me.good_fruit >= 75:
                return WindowCard.XIAN_GONG
        if opp == WindowCard.QIANG_XING:
            if me.has_resource("PASS_TOKEN") or me.has_resource("OFFICIAL_PERMIT"):
                return WindowCard.YAN_DIE
            if me.guard_points > 0:
                return WindowCard.BING_ZHENG
        if opp == WindowCard.XIAN_GONG:
            if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") or me.has_resource("FAST_HORSE") or me.has_resource("SHORT_HORSE"):
                return WindowCard.QIANG_XING
        if opp == WindowCard.BING_ZHENG:
            if high_value and me.freshness >= 85 and me.good_fruit >= 85:
                return WindowCard.XIAN_GONG
        return None


class RoadMasterStrategy:
    """Fast road-route strategy: stick to the main road, grab all tasks and key resources, sprint at 90."""

    def __init__(self, player_id: str, config: StrategyConfig, logger: DecisionLogger) -> None:
        self.player_id = player_id
        self.config = config
        self.logger = logger
        self.route_planner = RoutePlanner()
        self.window_strategy = WindowStrategy()
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
        self._scout_dispatched: set[str] = set()
        self._last_ice_box_frame: int = -100
        self._horse_used: bool = False

    def on_start(self, start_data: dict) -> None:
        self.logger.info("strategy_start", nodes=len(start_data.get("nodes", []) or []),
                         edges=len(start_data.get("edges", []) or []), strategy="ROAD_MASTER")

    def decide(self, state: GameState) -> ActionBundle:
        self._learn_from_feedback(state)
        self._track_station(state)
        if state.me.station != self._last_station:
            self._completed_fixed_process_nodes.discard(state.me.station)
            self._pending_process_until.pop(state.me.station, None)
        self._last_station = state.me.station
        decision = self._decide(state)
        self._remember_outbound_process(state, decision.bundle)
        return decision.bundle

    def _decide(self, state: GameState) -> Decision:
        me = state.me
        win_action, win_reason = self._optional_window(state)

        def done(b: ActionBundle, reason: str) -> Decision:
            r = reason if win_reason is None else f"{reason}+{win_reason}"
            return Decision(self._attach_win(b, win_action), r)

        if me.delivered or me.status == ConvoyStatus.DELIVERED:
            return done(wait("done"), "delivered")
        if me.retired:
            return done(wait("ret"), "retired")

        if me.status in MOVING_STATES or (me.status == ConvoyStatus.WAITING and me.route_edge_id):
            horse = self._moving_horse(state)
            if horse:
                return done(horse, "horse")
            return done(wait("m", active=False), "m")

        if me.status in BUSY_STATES or me.current_process is not None:
            return done(wait("b", active=False), "b")

        # Pending process wait
        pw = self._pending_process_wait(state)
        if pw:
            return done(pw, "pending_process")

        # === Always check for things we can do ===

        # 1. ICE_BOX when freshness drops
        if me.has_resource("ICE_BOX") and state.frame - self._last_ice_box_frame > 30:
            if me.freshness <= 88 or (me.freshness <= 92 and me.task_score_base >= 45):
                self._last_ice_box_frame = state.frame
                return done(ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="ICE_BOX")), "ice_box")

        # 2. At terminal: deliver
        if me.station == "S15":
            if me.verified and me.good_fruit > 0 and me.freshness > 0:
                return done(ActionBundle(main=MainAction(MainActionType.DELIVER)), "deliver")
            # Go back to gate
            return done(self._move_to(state, "S14"), "back_to_gate")

        # 3. At gate: verify
        if me.station == "S14":
            if not me.verified:
                if state.phase in RUSH_PHASES or state.frame > 390:
                    return done(self._do_verify(state), "verify")
                return done(wait("wait_gate", active=False), "wait_gate")
            return done(self._move_to(state, "S15"), "to_terminal")

        # 4. Fixed process on road stations
        fp = self._fixed_process(state)
        if fp:
            return done(fp, "process")

        # 5. Rush tactics
        rt = self._rush_tactic(state)
        if rt:
            return done(rt, "rush")

        # 6. Check if we need to lock delivery (sprint)
        if self._should_sprint(state):
            scout = self._scout(state)
            return done(self._move_to_gate(state, squad=scout), "sprint")

        # 7. Claim task at current station (high priority)
        station_task = self._station_task(state)
        if station_task:
            scout = self._scout(state, after=True)
            return done(self._claim_task(station_task, scout), f"task:{station_task.id}")

        # 8. Grab key resource at current station
        station_res = self._station_resource(state)
        if station_res:
            scout = self._scout(state, after=True)
            return done(self._claim_resource(station_res, scout), f"res:{station_res.resource_type}")

        # 9. Use horse before setting off
        if not me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED") and me.station in MAINLINE_STATIONS:
            if me.has_resource("FAST_HORSE"):
                return done(ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE")), "fast_horse")
            if me.has_resource("SHORT_HORSE") and me.station in {"S07", "S09", "S10"}:
                return done(ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE")), "short_horse")

        # 10. Move toward next road station or gate
        scout = self._scout(state)
        target = self._next_road_target(state)
        if target:
            return done(self._move_to(state, target, scout), f"move_{target}")
        return done(self._move_to_gate(state, scout), "move_gate")

    def _next_road_target(self, state: GameState) -> str | None:
        """Return the next station along the optimal road route."""
        me = state.me
        if not me.station:
            return None
        idx = ROAD_STATION_ORDER.get(me.station)
        if idx is None or idx >= len(ROAD_PATH) - 1:
            return None
        target = ROAD_PATH[idx + 1]

        # Don't skip fixed process stations
        if target in ROAD_PROCESS_STATIONS:
            return target

        # Check if we already completed this station's purpose
        if target in ROAD_RESOURCE_STATIONS:
            return target

        return target

    def _should_sprint(self, state: GameState) -> bool:
        me = state.me
        # Sprint if we hit 90 task base
        if me.task_score_base >= 90:
            return True
        # Sprint if endgame
        if state.phase in RUSH_PHASES:
            return True
        # Sprint if opponent is ahead
        if state.opponent and state.opponent.station:
            my_frames = self.route_planner.estimate_frames(state, me.station, "S14")
            opp_frames = self.route_planner.estimate_frames(state, state.opponent.station, "S14")
            if my_frames and opp_frames and opp_frames + 30 < my_frames:
                return True
        # Sprint if running out of time
        if me.station:
            to_gate = self.route_planner.estimate_frames(state, me.station, "S14")
            to_term = self.route_planner.estimate_frames(state, "S14", "S15")
            verify = 0 if me.verified else 6
            if to_gate and state.turns_left <= to_gate + to_term + verify + 45:
                return True
        return False

    def _fixed_process(self, state: GameState) -> ActionBundle | None:
        station_id = state.me.station
        if not station_id or station_id not in ROAD_PROCESS_STATIONS:
            return None
        if station_id in self._completed_fixed_process_nodes:
            return None
        if station_id in self._rejected_fixed_process_nodes:
            return None
        if state.me.current_process:
            return None
        return ActionBundle(main=MainAction(MainActionType.PROCESS, target=station_id))

    def _station_task(self, state: GameState) -> TaskInstance | None:
        if state.me.task_score_base >= 180:
            return None
        tasks = [t for t in state.station_tasks(state.me.station)
                 if t.id not in self._rejected_task_ids
                 and not self._is_cooldown(state, self._task_key(t.id))]
        if not tasks:
            return None
        # Pick the highest score task
        return max(tasks, key=lambda t: (t.score, -t.process_frames))

    def _station_resource(self, state: GameState) -> ResourceStock | None:
        stocks = [s for s in state.station_resources(state.me.station)
                  if (s.station, s.resource_type) not in self._rejected_resource_keys
                  and not self._is_cooldown(state, self._res_key(s.station, s.resource_type))]
        if not stocks:
            return None
        priority = {"ICE_BOX": 0, "FAST_HORSE": 1, "SHORT_HORSE": 2, "INTEL": 3,
                    "PASS_TOKEN": 4, "OFFICIAL_PERMIT": 5, "BOAT_RIGHT": 6}
        return min(stocks, key=lambda s: (priority.get(s.resource_type, 99), -s.amount))

    def _move_to_gate(self, state: GameState, squad: SquadAction | None = None) -> ActionBundle:
        target = "S15" if state.me.verified else "S14"
        return self._move_to_node(state, target, squad)

    def _move_to_node(self, state: GameState, target: str, squad: SquadAction | None = None) -> ActionBundle:
        if not state.me.station:
            return wait("no_station")
        hop = self.route_planner.next_hop_to_any(state, state.me.station, (target,))
        if not hop:
            return wait("no_route")
        return self._move_to(state, hop, squad)

    def _move_to(self, state: GameState, target: str, squad: SquadAction | None = None) -> ActionBundle:
        station = state.station(target)
        if station and station.has_obstacle:
            t04 = self._t04(state, target)
            if t04:
                return self._claim_task(t04)
            if state.me.squad_available >= 2 and state.phase not in RUSH_PHASES:
                return ActionBundle(squad=SquadAction(SquadActionType.SQUAD_CLEAR, target))
            if state.me.good_fruit >= 90:
                return ActionBundle(main=MainAction(MainActionType.CLEAR, target=target), squad=squad)
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=squad)

        if station and station.has_enemy_guard(state.me.team_id):
            bad = self._bad_fruit(state, station)
            if bad > 0:
                return ActionBundle(main=MainAction(MainActionType.BREAK_GUARD, target=target, good_fruit=0, bad_fruit=bad), squad=squad)
            if state.me.squad_available >= 2 and state.phase not in RUSH_PHASES:
                return ActionBundle(squad=SquadAction(SquadActionType.SQUAD_WEAKEN, target))
            return ActionBundle(main=MainAction(MainActionType.FORCED_PASS, target=target), squad=squad)

        return ActionBundle(main=MainAction(MainActionType.MOVE, target=target), squad=squad)

    def _bad_fruit(self, state, station):
        if state.me.bad_fruit <= 0:
            return 0
        needed = max(1, (station.guard_defense + 2) // 3)
        return needed if needed <= min(2, state.me.bad_fruit) else 0

    def _t04(self, state, target):
        for t in state.tasks:
            if t.template == "T04" and t.target == target and t.available_for(state.player_id) and t.id not in self._rejected_task_ids:
                return t
        return None

    def _do_verify(self, state: GameState) -> ActionBundle:
        rush = "BREAK_ORDER" if state.me.rush_tactic_used_count == 0 else None
        return ActionBundle(main=MainAction(MainActionType.VERIFY_GATE, target="S14", rush_tactic=rush))

    def _rush_tactic(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if state.phase not in RUSH_PHASES or me.rush_tactic_used_count > 0:
            return None
        if me.station in {None, "S14", "S15"}:
            return None
        if me.status not in PLANNING_STATES:
            return None
        # Use RUSH_PROTECT if tasks are good and freshness is at risk
        if me.task_score_base >= 60 and me.freshness <= 88:
            return ActionBundle(main=MainAction(MainActionType.RUSH_PROTECT))
        # Use RUSH_SPEED if no horse and deadline close
        if me.task_score_base >= 90 and me.good_fruit >= 88 and not me.has_resource("FAST_HORSE") and not me.has_resource("SHORT_HORSE"):
            remaining = self.route_planner.estimate_frames(state, me.station, "S15" if me.verified else "S14")
            if remaining and remaining >= 8 and state.turns_left <= remaining + 40:
                return ActionBundle(main=MainAction(MainActionType.RUSH_SPEED))
        return None

    def _moving_horse(self, state: GameState) -> ActionBundle | None:
        me = state.me
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        if me.has_resource("FAST_HORSE"):
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if me.has_resource("SHORT_HORSE"):
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return None

    def _scout(self, state: GameState, *, after: bool = False) -> SquadAction | None:
        if state.phase in RUSH_PHASES or state.me.squad_available <= 0 or not state.me.station:
            return None
        forbidden = {state.me.station, "S01", "S14", "S15"}
        plan = self.route_planner.plan(state, state.me.station, "S15" if state.me.verified else "S14")
        if not plan:
            return None
        for node in plan.path[1:4]:
            if node in forbidden or node in self._scout_dispatched:
                continue
            if node in ROAD_PROCESS_STATIONS or node in ROAD_RESOURCE_STATIONS:
                self._scout_dispatched.add(node)
                return SquadAction(SquadActionType.SQUAD_SCOUT, node)
        return None

    def _optional_window(self, state):
        window = state.active_window()
        if window is None:
            return None, None
        key = self._window_key(window) or f"W:{window.id}"
        seen = self._window_seen.get(key, 0) + 1
        self._window_seen[key] = seen
        if key in self._suppressed_window_keys or seen > 3:
            return None, None
        if self._is_cooldown(state, key):
            return None, None
        choice = self.window_strategy.choose(state, window, self.config)
        return WindowAction(window.id, choice.card), choice.card.value

    def _window_key(self, w):
        if w.task_id:
            return self._task_key(w.task_id)
        if w.target and w.resource_type:
            return self._res_key(w.target, w.resource_type)
        if w.id:
            return f"W:{w.id}"
        return None

    def _task_key(self, task_id):
        return f"T:{task_id}"

    def _res_key(self, station, rtype):
        return f"R:{station}:{rtype}"

    def _is_cooldown(self, state, key):
        until = self._object_cooldown_until.get(key)
        if until is None:
            return False
        if state.frame <= until:
            return True
        self._object_cooldown_until.pop(key, None)
        return False

    def _attach_win(self, bundle, win_act):
        if win_act is None or bundle.window is not None:
            return bundle
        return ActionBundle(main=bundle.main, squad=bundle.squad, window=win_act, debug=bundle.debug)

    def _claim_task(self, task, squad=None):
        return ActionBundle(main=MainAction(MainActionType.CLAIM_TASK, task_id=task.id), squad=squad)

    def _claim_resource(self, res, squad=None):
        return ActionBundle(main=MainAction(MainActionType.CLAIM_RESOURCE, target=res.station, resource_type=res.resource_type), squad=squad)

    def _track_station(self, state):
        station = state.me.station
        if not station:
            self._station_since_frame = None
            return
        if station != self._last_station or self._station_since_frame is None:
            self._station_since_frame = state.frame
            return
        if state.me.status not in PLANNING_STATES:
            return
        if station in MAINLINE_STATIONS:
            return
        stay = (self._station_since_frame is not None and state.frame - self._station_since_frame)
        if stay and stay >= 25 and not self._is_cooldown(state, f"ESCAPE:{station}"):
            self._object_cooldown_until[f"ESCAPE:{station}"] = state.frame + 999  # permanent
            self._station_escape_until[station] = state.frame + 20

    def _pending_process_wait(self, state):
        st = state.me.station
        if not st or st not in self._pending_process_until:
            return None
        if st in self._completed_fixed_process_nodes:
            self._pending_process_until.pop(st, None)
            return None
        if state.frame <= self._pending_process_until[st]:
            return wait("pp", active=False)
        self._pending_process_until.pop(st, None)
        return None

    def _remember_outbound_process(self, state, bundle):
        if bundle.main is None or bundle.main.action != MainActionType.PROCESS:
            return
        target = bundle.main.target or state.me.station
        if target:
            until = state.frame + (ROAD_PROCESS_STATIONS.get(target, 4)) + 5
            self._pending_process_until[target] = max(self._pending_process_until.get(target, 0), until)
            self._completed_fixed_process_nodes.discard(target)

    # ── Feedback learning (unchanged core) ──

    def _learn_from_feedback(self, state):
        for event in state.events:
            if not isinstance(event, dict):
                continue
            if not self._belongs(state, event):
                continue
            etype = str(event.get("event") or event.get("type") or "").upper()
            payload = event.get("payload") or {}
            node = event.get("targetNodeId") or event.get("nodeId") or payload.get("targetNodeId") or payload.get("nodeId") or state.me.station
            task_id = event.get("taskId") or payload.get("taskId")
            rtype = event.get("resourceType") or payload.get("resourceType")
            code = str(event.get("errorCode") or payload.get("errorCode") or "").upper()
            action = str(event.get("action") or payload.get("action") or "").upper()
            if code:
                self._learn_code(state, action, code, node, task_id, rtype)
            if etype in {"PROCESS_COMPLETE", "FIXED_PROCESS_COMPLETE", "PROCESS_COMPLETED"} and node:
                self._completed_fixed_process_nodes.add(str(node))
                self._pending_process_until.pop(str(node), None)
            if etype in {"TASK_COMPLETE"} and task_id:
                self._rejected_task_ids.discard(str(task_id))
        for result in state.action_results:
            if not isinstance(result, dict):
                continue
            if not self._belongs(state, result):
                continue
            action = str(result.get("action") or "").upper()
            code = str(result.get("errorCode") or "").upper()
            accepted = result.get("accepted")
            node = result.get("targetNodeId") or result.get("nodeId") or state.me.station
            task_id = result.get("taskId")
            rtype = result.get("resourceType")
            if code or accepted is False:
                self._learn_code(state, action, code, node, task_id, rtype)
            elif action == "PROCESS" and node:
                self._pending_process_until[str(node)] = state.frame + 10
            if action == "WINDOW_CARD" and (accepted is False or code):
                cid = str(result.get("contestId") or "")
                if cid:
                    self._suppressed_window_keys.add(f"W:{cid}")

    def _belongs(self, state, record):
        payload = record.get("payload") or {}
        vals = [record.get(k) for k in ("playerId", "actorPlayerId")]
        vals += [payload.get(k) for k in ("playerId", "actorPlayerId")]
        explicit = [v for v in vals if v not in (None, "")]
        if explicit:
            return any(str(v) == str(state.player_id) for v in explicit)
        return True

    def _learn_code(self, state, action, code, node, task_id, rtype):
        if not code:
            return
        node_s = str(node or state.me.station or "")
        if code in PROCESS_RETRY_CODES and node_s:
            self._forced_process_nodes.add(node_s)
        if code in PROCESS_HARD_REJECT_CODES and node_s:
            self._rejected_fixed_process_nodes.add(node_s)
        if action == "CLAIM_TASK" and task_id:
            self._rejected_task_ids.add(str(task_id))
            self._cooldown(state, self._task_key(str(task_id)))
        if action == "CLAIM_RESOURCE" and node and rtype:
            self._rejected_resource_keys.add((str(node), str(rtype)))
            self._cooldown(state, self._res_key(str(node), str(rtype)))

    def _cooldown(self, state, key, frames=30):
        until = state.frame + frames
        if self._object_cooldown_until.get(key, 0) < until:
            self._object_cooldown_until[key] = until
