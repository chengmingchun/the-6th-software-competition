"""Core game engine: simulates a complete match of 一骑红尘：荔枝争运战."""

from __future__ import annotations

import json
import os
import random
import math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from . import config as C


# ── Helper functions ──

def _edge_frames(distance: int, coefficient: int, speed: int = 1000, weather_mult: int = 1000) -> int:
    """Calculate frames needed to traverse an edge."""
    required_move = distance * coefficient
    per_frame = (speed * 1000) // max(weather_mult, 1)
    return max(1, (required_move + per_frame - 1) // per_frame)


def _move_progress(distance: int, coefficient: int, speed: int, weather_mult: int,
                   accumulated: int) -> tuple[int, float]:
    """Calculate remaining move progress. Returns (remaining_required, progress_ratio)."""
    required_move = distance * coefficient
    per_frame = (speed * 1000) // max(weather_mult, 1)
    remaining = max(0, required_move - accumulated)
    progress = min(1.0, accumulated / max(required_move, 1))
    return remaining, progress


def _freshness_kind(status: str, route_type: str | None) -> str:
    if status in ("IDLE", "WAITING", "PROCESSING", "VERIFYING", "CONTESTING", "RESTING", "COST_BANKRUPT"):
        return "STATIONARY"
    if status == "MOVING" and route_type:
        return route_type
    if status == "FORCED_PASSING":
        return "STATIONARY"  # extra wait frames
    return "STATIONARY"


def _pop_random(items: list, rng: random.Random) -> Any | None:
    if not items:
        return None
    idx = rng.randint(0, len(items) - 1)
    return items.pop(idx)


# ── Player state ──

@dataclass
class Player:
    player_id: str
    team_id: str = ""

    # Position & movement
    station: str = "S01"
    target_station: str | None = None
    route_edge: str | None = None
    route_type: str | None = None
    status: str = "IDLE"
    move_accumulated: int = 0  # accumulated move units on current edge
    move_edge_distance: int = 0
    move_edge_coefficient: int = 1380

    # Cargo
    good_fruit: int = 100
    frozen_good_fruit: int = 0
    bad_fruit: int = 0
    freshness: float = 100.0
    last_triggered_bad: set[int] = field(default_factory=set)

    # Resources
    resources: dict[str, int] = field(default_factory=lambda: {})

    # Buffs
    buffs: dict[str, int] = field(default_factory=dict)  # type -> remaining_frames

    # Squad
    squad_available: int = 8
    squad_in_flight: int = 0  # number of squads en route

    # Guard points
    guard_points: int = 4

    # Guards placed (node -> defense)
    guards: dict[str, GuardState] = field(default_factory=dict)

    # Verification & delivery
    verified: bool = False
    delivered: bool = False
    retired: bool = False
    deliver_round: int = 0

    # Score tracking
    task_score_base: int = 0
    task_score: int = 0  # actual score including milestones
    bounty_score: int = 0
    total_score: int = 0

    # Processing
    current_process: dict[str, Any] | None = None  # {type, target, frames_left, ...}
    process_interrupted: bool = False

    # Rush tactic tracking
    rush_tactic_used: int = 0  # 0 or 1

    # Penalty tracking
    illegal_action_count: int = 0
    post_deliver_penalty: int = 0

    # Cooldowns
    cooldowns: dict[str, int] = field(default_factory=dict)  # key -> frame until

    # Missing action count
    missing_action_frames: int = 0

    # Forced-pass repeat tracking
    last_forced_pass_node: str | None = None

    # Residual clearance tax extra wait frames
    additional_wait_frames: int = 0

    # Re-verify
    needs_reverify: bool = False

    # Processing for fixed process / claim / etc
    processing_frame_start: int = 0

    # Per-visit fixed process completion tracking (RESET on each arrival)
    fixed_process_completed_here: bool = False
    # Tracks whether the verify gate condition has been met before leaving S14
    # (Separate from p.verified which is the global verified flag)

    # Last frame action summary
    last_action: str | None = None
    last_action_accepted: bool = True
    last_action_result: str = "NONE"
    last_action_error: str | None = None

    def has_buff(self, *buff_types: str) -> bool:
        return any(b in self.buffs for b in buff_types)

    def has_resource(self, resource_type: str) -> bool:
        return self.resources.get(resource_type, 0) > 0


@dataclass
class GuardState:
    owner_team: str
    defense: int
    cap: int
    completed_frame: int
    last_wind_frame: int
    wind_interval: int
    is_key_pass: bool = False

    # Bounty tracking
    consecutive_bounty_frame: int = 0  # frames since setup
    opponent_attack_count: int = 0
    opponent_fail_count: int = 0

    # Whether bounty has been claimed
    bounty_claimed: bool = False


@dataclass
class TaskInstance:
    task_id: str
    template: str
    target: str
    score: int
    process_frames: int
    refresh_frame: int
    expire_frame: int

    # Consumes horse for T06
    requires_horse: bool = False

    # T04: obstacle clearing
    clears_obstacle: bool = False
    obstacle_target: str | None = None

    active: bool = True
    completed: bool = False
    failed: bool = False
    owner_player_id: int | str | None = None
    protection_player_id: int | str | None = None


@dataclass
class ContestWindow:
    contest_id: str
    contest_type: str  # RESOURCE / TASK / GATE / DOCK / PASS / OBSTACLE
    target_node: str | None = None
    resource_type: str | None = None
    task_id: str | None = None

    red_player_id: int | str | None = None
    blue_player_id: int | str | None = None
    initiator_player_id: int | str | None = None

    round_index: int = 1
    total_rounds: int = 3
    deadline_round: int = 0

    red_point: int = 0
    blue_point: int = 0

    red_card: str | None = None
    blue_card: str | None = None

    # Initial lock values for PASS
    initial_time_tax: int = 0
    initial_block_type: str = ""
    initial_guard_owner_team: str = ""
    initial_guard_defense: int = 0
    initial_has_obstacle: bool = False
    initial_obstacle_tax: int = 0

    resolved: bool = False
    winner_team: str | None = None  # None = pending, "DRAW" = draw

    # Suppression for repeated draws
    suppressed: bool = False
    suppress_until_round: int = 0
    object_key: str | None = None

    # Source actions
    source_action_types: dict[str, str] = field(default_factory=dict)
    source_task_ids: dict[str, str] = field(default_factory=dict)
    break_order_cost_types: dict[str, str] = field(default_factory=dict)


@dataclass
class ObstacleInfo:
    node_id: str
    obstacle_type: str
    neighbors: list[str]

    # T04 clearing
    cleared: bool = False
    clear_frame: int = 0
    clear_team: str | None = None  # who cleared (for tax)

    # Squad clearing in progress
    squad_clear_team: str | None = None
    squad_clear_arrival_frame: int = 0

    # Contest lock for repeated draws
    draw_count: int = 0
    draw_lock_until: int = 0


@dataclass
class WeatherEvent:
    weather_type: str
    start_frame: int
    duration: int
    region: list[str]

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.duration - 1


@dataclass
class ScoutMarker:
    team_id: str
    start_frame: int
    end_frame: int
    used: bool = False


# ── Event Log ──

@dataclass
class GameEvent:
    type: str
    round: int
    payload: dict[str, Any] = field(default_factory=dict)


# ── Main Engine ──

class GameEngine:
    """Simulates one match of 一骑红尘：荔枝争运战."""

    def __init__(self, match_id: str = "match_local_001",
                 seed: int = 42,
                 player1_id: int | str = 1001,
                 player2_id: int | str = 1002,
                 player1_name: str = "Player-RED",
                 player2_name: str = "Player-BLUE",
                 scenario: str | None = None) -> None:
        self.match_id = match_id
        self.seed = seed
        self.rng = random.Random(seed)
        self.scenario = (scenario or os.environ.get("LIZHI_SERVER_SCENARIO") or "").strip().lower()

        self.frame = 0
        self.phase = "NORMAL"
        self.started = False
        self.ended = False

        # Players
        self.players: dict[str, Player] = {}
        self.player1_id = str(player1_id)
        self.player2_id = str(player2_id)
        self.team_map: dict[str, str] = {}  # player_id -> team_id
        self._init_players()

        # Stations (node state)
        self.stations: dict[str, dict[str, Any]] = {}
        self.obstacles: dict[str, ObstacleInfo] = {}
        self.scout_markers: dict[str, list[ScoutMarker]] = {}  # node -> markers
        self._init_stations()

        # Edges
        self.edges: list[dict[str, Any]] = C.EDGE_DEFS

        # Resources
        self.resource_stock: dict[str, dict[str, int]] = {}  # node -> {type: count}
        self._init_resources()

        # Tasks
        self.tasks: list[TaskInstance] = []
        self._task_counter = 0
        self._last_task_refresh = 0

        # Contests / windows
        self.contests: list[ContestWindow] = []
        self._contest_counter = 0

        # Weather
        self.weather_events: list[WeatherEvent] = []
        self.current_weather: WeatherEvent | None = None
        self._init_weather()

        # Events log
        self.events: list[GameEvent] = []

        # Action results log
        self.action_results: list[dict[str, Any]] = []

        # Players' last actions
        self.player_actions: dict[str, list[dict[str, Any]]] = {
            self.player1_id: [],
            self.player2_id: [],
        }

        # Player connection status
        self.player_connected: dict[str, bool] = {self.player1_id: False, self.player2_id: False}

        # Counter for draw suppression
        self.draw_suppress: dict[str, int] = {}  # object_key -> until frame

        # Bounty tracking
        self.bounties: list[dict[str, Any]] = []
        self._bounty_counter = 0

        # Score preview
        self.score_preview: dict[str, int] = {"RED": 0, "BLUE": 0}

        # Scenario bookkeeping.  These hooks are intentionally opt-in: the
        # default local engine stays a neutral rules simulator, while regression
        # scenarios can inject online-like pressure such as key-pass guards.
        self._scenario_guard_installed: set[tuple[str, str]] = set()

    def _init_players(self) -> None:
        # Randomly assign RED/BLUE
        teams = ["RED", "BLUE"]
        self.rng.shuffle(teams)

        p1 = Player(player_id=self.player1_id, team_id=teams[0])
        p2 = Player(player_id=self.player2_id, team_id=teams[1])
        self.players[self.player1_id] = p1
        self.players[self.player2_id] = p2
        self.team_map[self.player1_id] = teams[0]
        self.team_map[self.player2_id] = teams[1]

    def _init_stations(self) -> None:
        for nid, info in C.NODE_INFO.items():
            self.stations[nid] = {
                "nodeId": nid,
                "name": info["name"],
                "x": info["x"],
                "y": info["y"],
                "nodeType": info["type"],
                "type": info["type"],
                "code": info["code"],
                "hasObstacle": False,
                "canWindow": True,
                "resourceStock": {},
            }
        # Obstacles
        for obs_node in C.OBSTACLE_CANDIDATES:
            self.obstacles[obs_node] = ObstacleInfo(
                node_id=obs_node,
                obstacle_type="LANDSLIDE",
                neighbors=C.OBSTACLE_CANDIDATES[obs_node],
                cleared=False,
            )
        # Mark stations with obstacles
        for nid in self.obstacles:
            self.stations[nid]["hasObstacle"] = True

    def _init_resources(self) -> None:
        for node, resources in C.INITIAL_RESOURCES.items():
            self.resource_stock[node] = dict(resources)
            for res_type, count in resources.items():
                if count > 0:
                    if "resourceStock" not in self.stations[node]:
                        self.stations[node]["resourceStock"] = {}
                    self.stations[node]["resourceStock"][res_type] = count

    def _init_weather(self) -> None:
        for ws in C.WEATHER_SCHEDULE:
            start = self.rng.randint(ws["start_range"][0], ws["start_range"][1])
            self.weather_events.append(WeatherEvent(
                weather_type=ws["type"],
                start_frame=start,
                duration=ws["duration"],
                region=ws["region"],
            ))

    def reset_seed(self, new_seed: int) -> None:
        self.seed = new_seed
        self.rng = random.Random(new_seed)

    # ── Public API ──

    def get_start_payload(self, player_id: str) -> dict[str, Any]:
        """Generate the start message for a player."""
        p = self.players[player_id]
        team = self.team_map[player_id]
        opponent_id = self._opponent_id(player_id)

        nodes_list = []
        for nid, info in C.NODE_INFO.items():
            node = {
                "nodeId": nid,
                "name": info["name"],
                "x": info["x"],
                "y": info["y"],
                "nodeType": info["type"],
                "type": info["type"],
                "code": info["code"],
                "start": nid == "S01",
                "terminal": nid == "S15",
                "icon": "",
            }
            if nid in C.FIXED_PROCESS_NODES:
                pt, pr, cw = C.FIXED_PROCESS_NODES[nid]
                node["processType"] = pt
                node["processRound"] = pr
                node["canWindow"] = cw
            nodes_list.append(node)

        edges_list = []
        for e in C.EDGE_DEFS:
            edges_list.append({
                "edgeId": e["id"],
                "fromNode": e["from"],
                "fromNodeId": e["from"],
                "toNode": e["to"],
                "toNodeId": e["to"],
                "routeType": e["type"],
                "distance": e["dist"],
                "bidirectional": True,
                "pathId": f"P_{e['id']}",
            })

        resources_list = []
        for node, res_map in C.INITIAL_RESOURCES.items():
            for rt, cnt in res_map.items():
                resources_list.append({
                    "nodeId": node,
                    "resourceType": rt,
                    "count": cnt,
                    "claimRound": C.RESOURCE_CLAIM_FRAMES,
                })

        task_templates_list = []
        for tid, (score, pf, candidates) in C.TASK_TEMPLATES.items():
            task_templates_list.append({
                "taskTemplateId": tid,
                "name": f"Task-{tid}",
                "candidateNodeIds": candidates,
                "processType": "CLAIM_TASK",
                "processRound": pf,
                "score": score,
                "requiredFreshness": 0,
                "requiredResourceTypes": [],
            })

        gameplay = {
            "roles": {
                "startNodeId": "S01",
                "gateNodeId": "S14",
                "terminalNodeIds": ["S15"],
                "safeZoneNodeIds": ["S15"],
                "reverifyNodeId": "S15",
            },
            "resources": resources_list,
            "processNodes": [
                {"nodeId": nid, "processType": pt, "processRound": pr, "canWindow": cw}
                for nid, (pt, pr, cw) in C.FIXED_PROCESS_NODES.items()
            ],
            "taskCandidates": {tid: cand for tid, (_, _, cand) in C.TASK_TEMPLATES.items()},
            "routeTaskBuckets": {
                "ROAD": ["S03", "S07"],
                "WATER": ["S04", "S05"],
                "MOUNTAIN": ["S06", "S08"],
                "BRANCH": ["S10", "S11"],
            },
            "obstacleCandidateNodeIds": list(C.OBSTACLE_CANDIDATES.keys()),
        }

        return {
            "msg_name": "start",
            "msg_data": {
                "matchId": self.match_id,
                "rulesVersion": "4.1",
                "round": 1,
                "tick": 0,
                "durationRound": C.MAX_FRAMES,
                "map": {
                    "mapId": "litchi_map_competition",
                    "mapName": "一骑红尘：荔枝争运战竞技地图",
                    "maxX": 80,
                    "maxY": 60,
                    "gameplay": gameplay,
                    "data": "",
                },
                "players": [
                    {"playerId": self.player1_id, "camp": 0, "teamId": self.team_map[self.player1_id], "name": p.player_id},
                    {"playerId": self.player2_id, "camp": 1, "teamId": self.team_map[self.player2_id], "name": self.players[opponent_id].player_id if opponent_id else opponent_id},
                ],
                "nodes": nodes_list,
                "edges": edges_list,
                "routePaths": [],
                "resources": resources_list,
                "taskTemplates": task_templates_list,
            },
        }

    def get_inquire_payload(self, round_no: int) -> dict[str, Any]:
        """Generate the inquire message for a given frame."""
        p1 = self.players[self.player1_id]
        p2 = self.players[self.player2_id]

        players_list = []
        for pid, p in [(self.player1_id, p1), (self.player2_id, p2)]:
            player_data = self._player_inquire_data(pid)
            players_list.append(player_data)

        # Nodes state
        nodes_list = []
        for nid in sorted(self.stations.keys()):
            snode = self.stations[nid]
            obs = self.obstacles.get(nid)
            guard_info = self._node_guard_info(nid)
            node_data = {
                "nodeId": nid,
                "name": snode.get("name", ""),
                "nodeType": snode.get("nodeType", ""),
                "type": snode.get("type", ""),
                "hasObstacle": obs is not None and not obs.cleared,
                "canWindow": True,
            }
            if guard_info:
                node_data["guard"] = guard_info
            # Resource stock
            stock = self.resource_stock.get(nid, {})
            if stock:
                node_data["resourceStock"] = dict(stock)
            # Scout markers
            markers = self.scout_markers.get(nid, [])
            active_markers = [m for m in markers if m.end_frame >= round_no and not m.used]
            if active_markers:
                node_data["scouted"] = [
                    {"teamId": m.team_id, "remainingTriggers": 1, "endFrame": m.end_frame}
                    for m in active_markers
                ]
            nodes_list.append(node_data)

        # Tasks
        tasks_list = []
        for t in self.tasks:
            if t.active and not t.completed and not t.failed:
                tasks_list.append({
                    "taskId": t.task_id,
                    "taskTemplateId": t.template,
                    "name": f"Task-{t.template}-{t.task_id}",
                    "nodeId": t.target,
                    "routeBucket": "",
                    "processType": "CLAIM_TASK",
                    "processRound": t.process_frames,
                    "score": t.score,
                    "refreshRound": t.refresh_frame,
                    "expireRound": t.expire_frame,
                    "active": t.active,
                    "completed": False,
                    "failed": False,
                    "ownerPlayerId": t.owner_player_id or 0,
                    "protectionPlayerId": t.protection_player_id or 0,
                })

        # Weather
        weather_data: dict[str, Any] = {}
        if self.current_weather:
            weather_data["active"] = [{
                "type": self.current_weather.weather_type,
                "startRound": self.current_weather.start_frame,
                "endRound": self.current_weather.end_frame,
            }]
        # Forecast: next weather event within preview range
        for we in self.weather_events:
            if we.start_frame > round_no and we.start_frame - round_no <= C.WEATHER_PREVIEW_FRAMES:
                forecast_list = weather_data.get("forecast", [])
                forecast_list.append({
                    "type": we.weather_type,
                    "startRound": we.start_frame,
                    "duration": we.duration,
                })
                weather_data["forecast"] = forecast_list

        # Contests
        contests_list = []
        for c in self.contests:
            cdata = {
                "contestId": c.contest_id,
                "contestType": c.contest_type,
                "targetNodeId": c.target_node,
                "redPlayerId": c.red_player_id,
                "bluePlayerId": c.blue_player_id,
                "roundIndex": c.round_index,
                "totalRounds": c.total_rounds,
                "deadlineRound": c.deadline_round,
                "redPoint": c.red_point,
                "bluePoint": c.blue_point,
                "resolved": c.resolved,
            }
            if c.resource_type:
                cdata["resourceType"] = c.resource_type
            if c.task_id:
                cdata["taskId"] = c.task_id
            if c.initiator_player_id:
                cdata["initiatorPlayerId"] = c.initiator_player_id
            if c.source_action_types:
                cdata["sourceActionTypes"] = c.source_action_types
            if c.source_task_ids:
                cdata["sourceTaskIds"] = c.source_task_ids
            if c.break_order_cost_types:
                cdata["breakOrderCostTypes"] = c.break_order_cost_types
            if c.initial_time_tax > 0:
                cdata["initialTimeTaxRound"] = c.initial_time_tax
            if c.initial_block_type:
                cdata["initialBlockType"] = c.initial_block_type
            if c.initial_guard_owner_team:
                cdata["initialGuardOwnerTeamId"] = c.initial_guard_owner_team
            if c.initial_guard_defense > 0:
                cdata["initialGuardDefense"] = c.initial_guard_defense
            if c.initial_has_obstacle:
                cdata["initialObstacle"] = True
                cdata["initialObstacleTaxRound"] = c.initial_obstacle_tax
            if c.winner_team:
                cdata["winnerTeamId"] = c.winner_team
            if c.suppressed:
                cdata["status"] = "SUPPRESSED"
                cdata["objectKey"] = c.object_key
                cdata["suppressUntilRound"] = c.suppress_until_round
                cdata["remainRound"] = max(0, c.suppress_until_round - round_no)
            contests_list.append(cdata)

        # Events (only events from last round)
        recent_events = [
            {"type": e.type, "round": e.round, "payload": e.payload}
            for e in self.events
            if e.round == round_no - 1 or e.round == round_no
        ]

        # Action results
        action_results = [
            r for r in self.action_results
            if r.get("round") == round_no - 1
        ]

        # Score preview
        score_preview = {
            "RED": self._calc_preview_score("RED"),
            "BLUE": self._calc_preview_score("BLUE"),
        }

        return {
            "msg_name": "inquire",
            "msg_data": {
                "matchId": self.match_id,
                "round": round_no,
                "tick": round_no - 1,
                "phase": self.phase,
                "players": players_list,
                "nodes": nodes_list,
                "edges": self.edges,
                "weather": weather_data if weather_data else {},
                "tasks": tasks_list,
                "bounties": self._bounties_data(),
                "contests": contests_list,
                "events": recent_events,
                "actionResults": action_results,
                "scorePreview": score_preview,
            },
        }

    def _player_inquire_data(self, player_id: str) -> dict[str, Any]:
        p = self.players[player_id]
        data: dict[str, Any] = {
            "playerId": player_id,
            "teamId": p.team_id,
            "state": p.status,
            "currentNodeId": p.station,
            "nextNodeId": p.target_station,
            "routeEdgeId": p.route_edge,
            "routeType": p.route_type,
            "goodFruit": p.good_fruit,
            "frozenGoodFruit": p.frozen_good_fruit,
            "badFruit": p.bad_fruit,
            "freshness": round(p.freshness, 2),
            "taskScore": p.task_score_base,
            "taskScoreBase": p.task_score_base,
            "bountyScore": p.bounty_score,
            "totalScore": p.total_score,
            "delivered": p.delivered,
            "verified": p.verified,
            "retired": p.retired,
            "resources": dict(p.resources),
            "squadAvailable": p.squad_available,
            "squadInFlight": p.squad_in_flight,
            "guardActionPoint": p.guard_points,
            "rushTacticUsedCount": p.rush_tactic_used,
        }
        # Buffs
        buff_list = []
        for btype, remaining in p.buffs.items():
            if remaining > 0:
                buff_list.append({"type": btype, "remaining": remaining})
        if buff_list:
            data["buffs"] = buff_list
        # Current process
        if p.current_process:
            data["currentProcess"] = p.current_process
        # Move progress
        if p.status == "MOVING" and p.route_edge:
            edge = self._edge_by_id(p.route_edge)
            if edge:
                coeff = C.ROUTE_COEFFICIENT.get(edge["type"], 1380)
                required = edge["dist"] * coeff
                data["moveProgress"] = min(1.0, p.move_accumulated / max(required, 1))
        return data

    def _node_guard_info(self, nid: str) -> dict[str, Any] | None:
        for pid, p in self.players.items():
            if nid in p.guards:
                g = p.guards[nid]
                if g.defense > 0:
                    return {"ownerTeamId": g.owner_team, "defense": g.defense}
        return None

    def _bounties_data(self) -> list[dict[str, Any]]:
        result = []
        for b in self.bounties:
            result.append({
                "bountyId": b["id"],
                "bountyType": b.get("type", "NORMAL_BOUNTY"),
                "nodeId": b.get("nodeId"),
                "ownerTeamId": b.get("ownerTeam"),
                "triggerRound": b.get("triggerRound", 0),
                "rewardScore": b.get("rewardScore", 0),
                "active": b.get("active", True),
                "completed": b.get("completed", False),
                "winnerPlayerId": b.get("winnerPlayerId", 0),
            })
        return result

    def _calc_preview_score(self, team: str) -> int:
        # Simple preview: sum of tasks + bounty + estimates
        score = 0
        for pid, p in self.players.items():
            if p.team_id == team:
                score += p.task_score_base + p.bounty_score
                if p.delivered or p.verified:
                    score += 120  # base delivery
                    score += int(p.good_fruit / 100 * 180)
                    score += int(p.freshness / 100 * 180)
                break
        return score

    # ── Process actions ──

    def process_actions(self, frame: int,
                        actions1: list[dict[str, Any]],
                        actions2: list[dict[str, Any]]) -> None:
        """Process both players' actions for a frame."""
        self.frame = frame
        self.events.clear()
        self.action_results.clear()
        self._apply_scenario_events(frame)

        p1 = self.players[self.player1_id]
        p2 = self.players[self.player2_id]

        # Track if players sent actions
        for pid, acts in [(self.player1_id, actions1), (self.player2_id, actions2)]:
            if acts is not None:
                self.players[pid].missing_action_frames = 0
            else:
                self.players[pid].missing_action_frames += 1
                acts = []
            self.player_actions[pid] = acts

        # Check retirement
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]
            if p.missing_action_frames >= 60 and not p.delivered and not p.retired:
                p.retired = True
                self._add_event("PLAYER_RETIRED", {"playerId": pid, "reason": "MISSING_ACTIONS"})

        # Categorize actions
        main_actions: dict[str, dict[str, Any] | None] = {self.player1_id: None, self.player2_id: None}
        squad_actions: dict[str, dict[str, Any] | None] = {self.player1_id: None, self.player2_id: None}
        window_actions: dict[str, dict[str, Any] | None] = {self.player1_id: None, self.player2_id: None}

        for pid in [self.player1_id, self.player2_id]:
            acts = self.player_actions[pid]
            if not acts:
                continue
            # Categorize by action type
            main_count = 0
            squad_count = 0
            window_count = 0
            for act in acts:
                atype = act.get("action", "")
                if atype in ("WINDOW_CARD",):
                    window_count += 1
                    window_actions[pid] = act
                elif atype.startswith("SQUAD_"):
                    squad_count += 1
                    squad_actions[pid] = act
                else:
                    main_count += 1
                    main_actions[pid] = act

            # Check conflict: >1 of same type
            if main_count > 1:
                main_actions[pid] = None
                self._add_action_result(pid, "CONFLICT", False, "INVALID_ACTION_CONFLICT")
            if squad_count > 1:
                squad_actions[pid] = None
                self._add_action_result(pid, "SQUAD_CONFLICT", False, "INVALID_ACTION_CONFLICT")
            if window_count > 1:
                window_actions[pid] = None
                self._add_action_result(pid, "WINDOW_CONFLICT", False, "INVALID_ACTION_CONFLICT")

        # 1. Process window cards first (they happen alongside main actions)
        self._process_windows(frame, window_actions)

        # 2. Process squad actions before main actions. The online protocol
        # evaluates same-frame action slots from the frame-start state, so a
        # legal IDLE packet containing MOVE + SQUAD_SCOUT must not become
        # illegal merely because MOVE mutates the local state to MOVING first.
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]
            if p.retired:
                continue
            act = squad_actions[pid]
            if act is not None and not p.delivered:
                self._process_squad_action(pid, act)

        # 3. Process main actions
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]
            if p.delivered:
                # Delivered players can only WAIT or DELIVER
                act = main_actions[pid]
                if act and act.get("action") not in ("WAIT", "DELIVER"):
                    # Post-deliver violation
                    p.post_deliver_penalty += 1
                    self._add_action_result(pid, "POST_DELIVER_VIOLATION", False, "ALREADY_DELIVERED",
                                            error="DELIVERED_ACTION_FORBIDDEN")
                    main_actions[pid] = None
            if p.retired:
                main_actions[pid] = None
                squad_actions[pid] = None
                window_actions[pid] = None

        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]
            act = main_actions[pid]
            if act is None:
                p.last_action = "WAIT"
                p.last_action_accepted = True
                p.last_action_result = "SYSTEM_WAIT"
                p.last_action_error = None
                continue

            atype = act.get("action", "")
            self._process_main_action(pid, atype, act)

        # 4. Process in-flight squads arriving this frame
        self._process_arriving_squads(frame)

        # 5. Advance player states (movement, processing, etc.)
        self._advance_states(frame)

        # 6. Apply freshness decay
        self._apply_freshness(frame)

        # 7. Check bad fruit conversion
        for pid in [self.player1_id, self.player2_id]:
            self._check_bad_fruit_conversion(pid)

        # 8. Wind guards (reduce defense over time)
        self._wind_guards(frame)

        # 9. Check bounties
        self._check_bounties(frame)

        # 10. Refresh tasks
        if frame - self._last_task_refresh >= C.TASK_REFRESH_INTERVAL:
            self._refresh_tasks(frame)
            self._last_task_refresh = frame

        # 11. Expire tasks
        self._expire_tasks(frame)

        # 12. Update weather
        self._update_weather(frame)

        # 13. Check RUSH phase trigger
        self._check_rush_trigger(frame)

        # 14. Update score preview
        self._update_scores(frame)

        # 15. Check end conditions
        self._check_end_conditions(frame)

    def _apply_scenario_events(self, frame: int) -> None:
        """Optional scripted hazards for reproducing online failure modes.

        The normal engine only creates guards when a bot submits SET_GUARD. That
        is faithful, but it means self-play between two passive bots may never
        test the recovery path for enemy guard blocks. The guard_gauntlet
        scenario injects opponent-owned S10/S11 guards once player1 is close,
        matching the old logs where repeated MOVE into a guarded key pass caused
        a late-game stall.
        """

        if self.scenario not in {"guard_gauntlet", "online_guard_regression"}:
            return
        victim = self.players.get(self.player1_id)
        defender = self.players.get(self.player2_id)
        if victim is None or defender is None or victim.retired or victim.delivered:
            return
        schedule = (
            ("S09", "S10", 5, 280),
            ("S10", "S11", 4, 360),
        )
        for approach, target, defense, earliest_frame in schedule:
            key = (defender.player_id, target)
            if key in self._scenario_guard_installed:
                continue
            if frame < earliest_frame or victim.station != approach:
                continue
            self._install_scenario_guard(defender.player_id, target, defense, frame)
            self._scenario_guard_installed.add(key)

    def _install_scenario_guard(self, owner_pid: str, node_id: str, defense: int, frame: int) -> None:
        owner = self.players[owner_pid]
        node_info = C.NODE_INFO.get(node_id, {})
        cap = C.GUARD_DEFENSE_CAP["KEY_PASS"] if node_info.get("type") == "KEY_PASS" else C.GUARD_DEFENSE_CAP["default"]
        obs = self.obstacles.get(node_id)
        if obs is not None and not obs.cleared:
            obs.cleared = True
            obs.clear_frame = frame
            obs.clear_team = owner_pid
            self.stations[node_id]["hasObstacle"] = False
            self._add_event("SCENARIO_OBSTACLE_CLEARED", {"playerId": owner_pid, "nodeId": node_id})
        owner.guards[node_id] = GuardState(
            owner_team=owner.team_id,
            defense=min(defense, cap),
            cap=cap,
            completed_frame=frame,
            last_wind_frame=frame,
            wind_interval=C.GUARD_KEY_PASS_EXTRA_FIRST_WIND if node_info.get("type") == "KEY_PASS" and defense >= 4 else C.GUARD_WIND_INTERVAL,
            is_key_pass=node_info.get("type") == "KEY_PASS",
        )
        self._add_event(
            "SCENARIO_GUARD_SET",
            {"playerId": owner_pid, "teamId": owner.team_id, "nodeId": node_id, "defense": min(defense, cap)},
        )

    def _process_main_action(self, pid: str, atype: str, act: dict[str, Any]) -> None:
        p = self.players[pid]

        if p.status == "MOVING" and not self._main_action_allowed_while_moving(p, atype, act):
            self._reject_illegal_action(pid, atype, "STATE_MOVING_FORBIDDEN",
                                        error="Main convoy action is restricted while MOVING")
            return

        # Check if can act (not busy)
        if p.status in ("PROCESSING", "VERIFYING", "RESTING", "FORCED_PASSING", "CONTESTING", "COST_BANKRUPT"):
            self._add_action_result(pid, atype, False, f"STATE_{p.status}_FORBIDDEN",
                                    error=f"Cannot act while {p.status}")
            p.last_action = atype
            p.last_action_accepted = False
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = f"STATE_{p.status}_FORBIDDEN"
            return

        # Dispatch
        handler = getattr(self, f"_do_{atype.lower()}", None)
        if handler:
            handler(pid, act)
        else:
            self._reject_illegal_action(pid, atype, "INVALID_ACTION_TYPE")

    def _main_action_allowed_while_moving(self, p: PlayerState, atype: str, act: dict[str, Any]) -> bool:
        if atype == "WAIT":
            return True
        if atype == "MOVE":
            return act.get("targetNodeId") == p.target_station
        if atype == "USE_RESOURCE":
            return act.get("resourceType") in {"FAST_HORSE", "SHORT_HORSE"}
        if atype == "RUSH_SPEED":
            return True
        return False

    def _do_wait(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        if p.route_edge:
            p.status = "WAITING"
        else:
            p.status = "IDLE"
        self._add_action_result(pid, "WAIT", True, "ACCEPTED")
        p.last_action = "WAIT"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_move(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        target = act.get("targetNodeId")

        if not target:
            self._add_action_result(pid, "MOVE", False, "MOVE_MISSING_TARGET")
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "MOVE_MISSING_TARGET"
            return

        # Check if target is a neighbor
        edge = self._find_edge(p.station, target)
        if edge is None:
            self._add_action_result(pid, "MOVE", False, "MOVE_EDGE_NOT_FOUND",
                                    error=f"No edge from {p.station} to {target}")
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "MOVE_EDGE_NOT_FOUND"
            return

        # Check if already moving on an edge — can only target current destination or branch from route start
        if p.status == "MOVING" and p.route_edge:
            if target != p.target_station:
                # Can go back to route start's other neighbors
                current_edge = self._edge_by_id(p.route_edge)
                if current_edge and current_edge["from"] == p.station:
                    # Already at start? shouldn't be MOVING
                    pass
                # Allow redirect: "改道" from route start's other neighbors
                start_node = self._edge_start(p.route_edge, p.station)
                if start_node and self._find_edge(start_node, target):
                    # Redirect: cancel current move, start new
                    p.move_accumulated = 0
                    p.route_edge = None
                    p.target_station = None
                    p.route_type = None
                else:
                    self._add_action_result(pid, "MOVE", False, "MOVE_EDGE_NOT_FOUND",
                                            error=f"Cannot redirect from route edge to {target}")
                    p.last_action_result = "ACTION_REJECTED"
                    p.last_action_error = "MOVE_EDGE_NOT_FOUND"
                    return

        # Special rule: S15 -> S14 return ignores obstacles and guards (任务书 2.3.1)
        is_return_from_terminal = (p.station == "S15" and target == "S14")

        # Check for obstacle
        obs = self.obstacles.get(target)
        if obs and not obs.cleared and not is_return_from_terminal:
            self._add_action_result(pid, "MOVE", False, "MOVE_BLOCKED_BY_OBSTACLE",
                                    error=f"Obstacle at {target}", targetNodeId=target, nodeId=target)
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "MOVE_BLOCKED_BY_OBSTACLE"
            return

        # Check for enemy guard
        if not is_return_from_terminal:
            for opid, op in self.players.items():
                if opid == pid:
                    continue
                if target in op.guards:
                    g = op.guards[target]
                    if g.defense > 0 and g.owner_team != p.team_id:
                        self._add_action_result(pid, "MOVE", False, "MOVE_BLOCKED_BY_GUARD",
                                                error=f"Enemy guard at {target}", targetNodeId=target, nodeId=target)
                        p.last_action_result = "ACTION_REJECTED"
                        p.last_action_error = "MOVE_BLOCKED_BY_GUARD"
                        return

        # Check fixed process requirement: cannot leave a fixed process node
        # without completing the required PROCESS first (VERIFY is handled separately).
        fixed_process_nodes_without_verify = {
            nid for nid in C.FIXED_PROCESS_NODES
            if C.FIXED_PROCESS_NODES[nid][0] != "VERIFY"
        }
        if p.station in fixed_process_nodes_without_verify and not p.fixed_process_completed_here:
            self._add_action_result(pid, "MOVE", False, "PROCESS_REQUIRED",
                                    error=f"Must complete PROCESS at {p.station} before leaving")
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "PROCESS_REQUIRED"
            return

        # S14 constraint: cannot leave S14 to S15 without being verified
        # (VERIFY_GATE is the only valid way, not a plain PROCESS)
        if p.station == "S14" and target == "S15" and not p.verified:
            self._add_action_result(pid, "MOVE", False, "VERIFY_REQUIRED",
                                    error="Must complete VERIFY_GATE at S14 before going to S15")
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "VERIFY_REQUIRED"
            return

        # Start move
        speed = self._effective_speed(p)
        weather_mult = self._weather_move_mult(edge["type"])
        coeff = C.ROUTE_COEFFICIENT.get(edge["type"], 1380)
        frames = _edge_frames(edge["dist"], coeff, speed, weather_mult)

        # Check for residual clearance tax (任务书 6.1.2)
        residual_tax = 0
        obs = self.obstacles.get(target)
        if obs and obs.cleared and obs.clear_team and obs.clear_team != pid:
            elapsed = self.frame - obs.clear_frame
            if 0 <= elapsed < C.CLEAR_RESIDUAL_TAX_FRAMES:
                residual_tax = C.CLEAR_RESIDUAL_TAX_DELAY
                self._add_event("RESIDUAL_TAX", {"playerId": pid, "nodeId": target,
                                                  "taxFrames": residual_tax})

        p.status = "MOVING"
        p.target_station = target
        p.route_edge = edge["id"]
        p.route_type = edge["type"]
        p.move_accumulated = 0
        p.move_edge_distance = edge["dist"]
        p.move_edge_coefficient = coeff
        p.additional_wait_frames = residual_tax

        self._add_action_result(pid, "MOVE", True, "ACCEPTED")
        p.last_action = "MOVE"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

        self._add_event("MOVE_PROGRESS", {
            "playerId": pid,
            "fromNodeId": p.station,
            "toNodeId": target,
            "routeEdgeId": edge["id"],
            "progress": 0.0,
            "edgeProgressMs": 0,
            "edgeTotalMs": frames * 1000,
        })

    def _do_process(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        target = act.get("targetNodeId") or p.station
        station = self.stations.get(target)

        if not station:
            self._add_action_result(pid, "PROCESS", False, "TARGET_NOT_FOUND")
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "TARGET_NOT_FOUND"
            return

        if target not in C.FIXED_PROCESS_NODES:
            self._add_action_result(pid, "PROCESS", False, "PROCESS_NOT_AVAILABLE")
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "PROCESS_NOT_AVAILABLE"
            return

        pt, pr, cw = C.FIXED_PROCESS_NODES[target]

        # Check position BEFORE consuming scout marker
        if p.station != target:
            self._add_action_result(pid, "PROCESS", False, "NOT_AT_TARGET_NODE")
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "NOT_AT_TARGET_NODE"
            return

        # All checks pass — now it's safe to consume scout marker
        # Apply weather extra frames for BOARD / WATER_TRANSFER in heavy rain
        weather_extra = 0
        if (self.current_weather and self.current_weather.weather_type == "HEAVY_RAIN"
                and pt in ("BOARD", "WATER_TRANSFER")):
            weather_extra = 4
        adjusted_pr = pr + weather_extra
        reduced_pr, _ = self._apply_scout_reduction(p, target, adjusted_pr)

        p.status = "PROCESSING"
        p.current_process = {
            "type": pt,
            "target": target,
            "framesLeft": reduced_pr,
            "totalFrames": reduced_pr,
        }
        p.processing_frame_start = self.frame

        self._add_action_result(pid, "PROCESS", True, "ACCEPTED")
        p.last_action = "PROCESS"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_claim_resource(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        target = act.get("targetNodeId") or p.station
        rtype = act.get("resourceType")

        if not rtype:
            self._add_action_result(pid, "CLAIM_RESOURCE", False, "MISSING_RESOURCE_TYPE")
            p.last_action_result = "ACTION_REJECTED"
            return

        if p.station != target:
            self._add_action_result(pid, "CLAIM_RESOURCE", False, "NOT_AT_TARGET_NODE")
            p.last_action_result = "ACTION_REJECTED"
            return

        stock = self.resource_stock.get(target, {})
        if rtype not in stock or stock[rtype] <= 0:
            self._add_action_result(pid, "CLAIM_RESOURCE", False, "RESOURCE_NOT_ENOUGH")
            p.last_action_result = "ACTION_REJECTED"
            return

        claim_frames = C.RESOURCE_CLAIM_FRAMES
        reduced_frames, _ = self._apply_scout_reduction(p, target, claim_frames)

        p.status = "PROCESSING"
        p.current_process = {
            "type": "CLAIM_RESOURCE",
            "target": target,
            "resourceType": rtype,
            "framesLeft": reduced_frames,
            "totalFrames": reduced_frames,
        }
        p.processing_frame_start = self.frame

        self._add_action_result(pid, "CLAIM_RESOURCE", True, "ACCEPTED")
        p.last_action = "CLAIM_RESOURCE"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_use_resource(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        rtype = act.get("resourceType")
        target = act.get("targetNodeId")

        if not rtype:
            self._add_action_result(pid, "USE_RESOURCE", False, "MISSING_RESOURCE_TYPE")
            p.last_action_result = "ACTION_REJECTED"
            return

        if p.resources.get(rtype, 0) <= 0:
            self._add_action_result(pid, "USE_RESOURCE", False, "RESOURCE_NOT_ENOUGH")
            p.last_action_result = "ACTION_REJECTED"
            return

        # Check usage rules per resource type
        if rtype == "ICE_BOX":
            if p.freshness <= 0:
                self._add_action_result(pid, "USE_RESOURCE", False, "FRESHNESS_ZERO")
                return
            p.freshness = min(C.FRESHNESS_MAX, p.freshness + 10)
            p.resources["ICE_BOX"] -= 1
            self._add_event("RESOURCE_USED", {"playerId": pid, "resourceType": "ICE_BOX", "freshnessAfter": p.freshness})

        elif rtype in ("FAST_HORSE", "SHORT_HORSE"):
            # Can't use if already has horse buff
            if p.has_buff("RUSH_SPEED"):
                self._add_action_result(pid, "USE_RESOURCE", False, "HORSE_BUFF_CONFLICT")
                return
            duration = 20 if rtype == "FAST_HORSE" else 14
            # Clear existing horse buffs if any
            for h in ("FAST_HORSE", "SHORT_HORSE"):
                p.buffs.pop(h, None)
            p.buffs[rtype] = duration
            p.resources[rtype] -= 1
            self._add_event("RESOURCE_USED", {"playerId": pid, "resourceType": rtype, "duration": duration})

        elif rtype == "INTEL":
            if p.status not in ("IDLE", "WAITING"):
                self._add_action_result(pid, "USE_RESOURCE", False, "NOT_AT_TARGET_NODE",
                                        error="Intel can only be used while stopped at a node")
                return
            if target:
                # Check distance
                dist = self._route_distance(p.station, target)
                if dist is None or dist > 15:
                    self._add_action_result(pid, "USE_RESOURCE", False, "TARGET_TOO_FAR")
                    return
                # Add scout marker
                if target not in self.scout_markers:
                    self.scout_markers[target] = []
                self.scout_markers[target].append(ScoutMarker(
                    team_id=p.team_id,
                    start_frame=self.frame,
                    end_frame=self.frame + 45,
                ))
                p.resources["INTEL"] -= 1
                self._add_event("SCOUT_MARKER_ADDED", {"playerId": pid, "targetNode": target, "duration": 45})
            else:
                self._add_action_result(pid, "USE_RESOURCE", False, "MISSING_TARGET")
                return

        elif rtype in ("PASS_TOKEN", "OFFICIAL_PERMIT", "BOAT_RIGHT"):
            # These are window cards / markers, don't consume actively
            self._add_action_result(pid, "USE_RESOURCE", False, "RESOURCE_NOT_USABLE")
            return

        self._add_action_result(pid, "USE_RESOURCE", True, "ACCEPTED")
        p.last_action = "USE_RESOURCE"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_claim_task(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        task_id = act.get("taskId")

        if not task_id:
            self._add_action_result(pid, "CLAIM_TASK", False, "MISSING_TASK_ID")
            return

        task = self._find_task(task_id)
        if task is None or task.completed or task.failed or not task.active:
            self._add_action_result(pid, "CLAIM_TASK", False, "TASK_NOT_FOUND")
            return

        # Check protection
        if task.protection_player_id and str(task.protection_player_id) != str(pid):
            self._add_action_result(pid, "CLAIM_TASK", False, "TASK_PROTECTED")
            return

        # Check position
        if task.template == "T04":
            # T04 can be done from adjacent node
            neighbors = [p.station] + self._neighbors(p.station)
            if task.target not in neighbors and task.target != p.station:
                self._add_action_result(pid, "CLAIM_TASK", False, "NOT_AT_TARGET_NODE")
                return
        else:
            if p.station != task.target:
                self._add_action_result(pid, "CLAIM_TASK", False, "NOT_AT_TARGET_NODE")
                return

        # T06 requires a horse
        if task.template == "T06":
            if not p.has_resource("FAST_HORSE") and not p.has_resource("SHORT_HORSE"):
                self._add_action_result(pid, "CLAIM_TASK", False, "TASK_REQUIREMENT_NOT_MET")
                return
            # Consume horse
            if p.resources.get("FAST_HORSE", 0) > 0:
                p.resources["FAST_HORSE"] -= 1
            else:
                p.resources["SHORT_HORSE"] -= 1

        # Apply scout marker reduction at the task's target node
        # T04 target is the obstacle node, which is where processing takes effect
        task_frames = task.process_frames
        reduced_task_frames, _ = self._apply_scout_reduction(p, task.target, task_frames)

        p.status = "PROCESSING"
        p.current_process = {
            "type": "CLAIM_TASK",
            "taskId": task_id,
            "template": task.template,
            "target": task.target,
            "framesLeft": reduced_task_frames,
            "totalFrames": reduced_task_frames,
            "score": task.score,
        }
        p.processing_frame_start = self.frame

        # Mark task as owned
        task.owner_player_id = pid

        self._add_action_result(pid, "CLAIM_TASK", True, "ACCEPTED")
        p.last_action = "CLAIM_TASK"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_deliver(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]

        if p.station != "S15":
            self._add_action_result(pid, "DELIVER", False, "DELIVER_NOT_AT_TERMINAL")
            return

        if not p.verified:
            self._add_action_result(pid, "DELIVER", False, "DELIVER_NOT_VERIFIED")
            return

        if p.delivered:
            self._add_action_result(pid, "DELIVER", False, "ALREADY_DELIVERED")
            return

        if p.good_fruit <= 0:
            self._add_action_result(pid, "DELIVER", False, "DELIVER_REQUIREMENT_NOT_MET",
                                    error="No good fruit")
            return

        if p.freshness <= 0:
            self._add_action_result(pid, "DELIVER", False, "DELIVER_REQUIREMENT_NOT_MET",
                                    error="No freshness")
            return

        p.delivered = True
        p.deliver_round = self.frame
        p.status = "DELIVERED"
        self._add_action_result(pid, "DELIVER", True, "ACCEPTED")
        self._add_event("PLAYER_DELIVERED", {"playerId": pid, "round": self.frame})
        p.last_action = "DELIVER"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_verify_gate(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]

        if self.phase not in ("RUSH", "BANQUET", "ENDGAME"):
            self._add_action_result(pid, "VERIFY_GATE", False, "NOT_IN_RUSH")
            return

        if p.station != "S14":
            self._add_action_result(pid, "VERIFY_GATE", False, "NOT_AT_GATE")
            return

        if p.verified:
            self._add_action_result(pid, "VERIFY_GATE", False, "ALREADY_VERIFIED")
            return

        # Check if can verify (not busy)
        if p.status in ("MOVING", "CONTESTING", "RESTING", "FORCED_PASSING", "PROCESSING"):
            self._add_action_result(pid, "VERIFY_GATE", False, f"STATE_{p.status}_FORBIDDEN")
            return

        # Start verification
        rush_tactic = act.get("rushTactic")
        verify_frames = 6
        if rush_tactic == "BREAK_ORDER" and p.rush_tactic_used == 0:
            # 破关令成本：坏果优先，至少2篓时消耗2篓坏果，否则消耗1篓好果
            if p.bad_fruit >= 2:
                p.bad_fruit -= 2
            elif p.bad_fruit == 1:
                p.bad_fruit -= 1
            elif p.good_fruit >= 1:
                p.good_fruit -= 1
            else:
                self._add_action_result(pid, "VERIFY_GATE", False, "RESOURCE_NOT_ENOUGH",
                                        error="Not enough bad/good fruit for BREAK_ORDER")
                return
            verify_frames = max(3, verify_frames - 3)
            p.rush_tactic_used = 1

        # Scout marker can further reduce verify time (stacking with BREAK_ORDER)
        reduced_frames, _ = self._apply_scout_reduction(p, "S14", verify_frames)

        p.status = "VERIFYING"
        p.current_process = {
            "type": "VERIFY",
            "target": "S14",
            "framesLeft": reduced_frames,
            "totalFrames": reduced_frames,
            "rushTactic": rush_tactic or "",
        }
        p.processing_frame_start = self.frame

        self._add_action_result(pid, "VERIFY_GATE", True, "ACCEPTED")
        p.last_action = "VERIFY_GATE"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_set_guard(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        target = act.get("targetNodeId") or p.station

        if target == "S15":
            self._add_action_result(pid, "SET_GUARD", False, "SAFE_ZONE_FORBIDDEN")
            return

        if p.station != target:
            self._add_action_result(pid, "SET_GUARD", False, "NOT_AT_TARGET_NODE")
            return

        # Check if guard already exists at node
        for opid, op in self.players.items():
            if target in op.guards and op.guards[target].defense > 0:
                self._add_action_result(pid, "SET_GUARD", False, "OBJECT_BUSY")
                return

        # Check guard limit (max 2 active guards)
        active_guards = sum(1 for g in p.guards.values() if g.defense > 0)
        if active_guards >= 2:
            # Remove the oldest one
            oldest_node = min(p.guards, key=lambda n: p.guards[n].completed_frame)
            del p.guards[oldest_node]
            self._add_event("GUARD_REMOVED", {"playerId": pid, "nodeId": oldest_node, "reason": "LIMIT"})

        extra = act.get("extraGoodFruit", 0)
        if extra not in (0, 1, 2):
            self._add_action_result(pid, "SET_GUARD", False, "PARAM_OUT_OF_RANGE")
            return

        # Calculate cap and base cost
        node_info = C.NODE_INFO.get(target, {})
        has_obs = target in self.obstacles and not self.obstacles[target].cleared
        ntype = node_info.get("type", "")
        if ntype == "KEY_PASS":
            cap = C.GUARD_DEFENSE_CAP["KEY_PASS"]
            base_cost = 1  # 关键关隘基础成本 1 篓好果
        elif ntype == "GATE":
            cap = C.GUARD_DEFENSE_CAP["GATE"]
            base_cost = 1  # 宫门基础成本 1 篓好果
        elif has_obs:
            cap = C.GUARD_DEFENSE_WITH_OBSTACLE
            base_cost = 0
        else:
            cap = C.GUARD_DEFENSE_CAP["default"]
            base_cost = 0

        total_cost = base_cost + extra
        if total_cost > 0 and p.good_fruit < total_cost:
            self._add_action_result(pid, "SET_GUARD", False, "RESOURCE_NOT_ENOUGH",
                                    error=f"Need {total_cost} good fruit for guard (base={base_cost}, extra={extra})")
            return

        defense = min(cap, 2 + extra * 2)

        # Determine wind interval
        is_key_pass = node_info.get("type") == "KEY_PASS"
        if is_key_pass and defense >= 4:
            first_wind = 45
        else:
            first_wind = 30
        wind_interval = 30

        p.status = "PROCESSING"
        p.current_process = {
            "type": "SET_GUARD",
            "target": target,
            "framesLeft": 4,
            "totalFrames": 4,
            "defense": defense,
            "cap": cap,
            "extraGoodFruit": extra,
            "baseCost": base_cost,
        }
        p.processing_frame_start = self.frame
        p.frozen_good_fruit += (base_cost + extra)

        self._add_action_result(pid, "SET_GUARD", True, "ACCEPTED")
        p.last_action = "SET_GUARD"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_break_guard(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        target = act.get("targetNodeId")
        good = act.get("goodFruit", 0)
        bad = act.get("badFruit", 0)

        if not target:
            self._add_action_result(pid, "BREAK_GUARD", False, "MISSING_TARGET")
            return

        if target == p.station:
            self._add_action_result(pid, "BREAK_GUARD", False, "SAME_NODE")
            return

        # Check adjacency
        if not self._find_edge(p.station, target):
            self._add_action_result(pid, "BREAK_GUARD", False, "TARGET_NOT_REACHABLE")
            return

        # Validate fruit count
        if good < 0 or good > 2 or bad < 0 or bad > 2:
            self._add_action_result(pid, "BREAK_GUARD", False, "PARAM_OUT_OF_RANGE")
            return

        if good > 0 and p.good_fruit < good:
            self._add_action_result(pid, "BREAK_GUARD", False, "RESOURCE_NOT_ENOUGH")
            return
        if bad > 0 and p.bad_fruit < bad:
            self._add_action_result(pid, "BREAK_GUARD", False, "RESOURCE_NOT_ENOUGH")
            return

        # Check enemy guard
        guard_owner = None
        guard_defense = 0
        for opid, op in self.players.items():
            if opid == pid:
                continue
            if target in op.guards and op.guards[target].defense > 0:
                guard_owner = opid
                guard_defense = op.guards[target].defense
                break

        if guard_owner is None or guard_defense <= 0:
            self._add_action_result(pid, "BREAK_GUARD", False, "NO_ENEMY_GUARD")
            return

        # Calculate break power
        break_power = good * 2 + bad * 3
        rush_tactic = act.get("rushTactic")
        if rush_tactic == "BREAK_ORDER" and p.rush_tactic_used == 0:
            if p.bad_fruit >= 2:
                p.bad_fruit -= 2
            elif p.bad_fruit == 1:
                p.bad_fruit -= 1
            elif p.good_fruit >= 1:
                p.good_fruit -= 1
            else:
                self._add_action_result(pid, "BREAK_GUARD", False, "RESOURCE_NOT_ENOUGH",
                                        error="Not enough bad/good fruit for BREAK_ORDER")
                return
            break_power += 3
            p.rush_tactic_used = 1

        if break_power <= 0:
            self._add_action_result(pid, "BREAK_GUARD", False, "NO_BREAK_POWER")
            return

        # Consume fruits
        p.good_fruit -= good
        p.bad_fruit -= bad

        op = self.players[guard_owner]
        guard = op.guards[target]

        # Check bounty eligibility before attack
        bounty_eligible = self._check_bounty_on_attack(pid, target, guard)

        if break_power >= guard_defense:
            # Success
            old_defense = guard_defense
            guard.defense = 0
            guard.bounty_claimed = False
            self._add_event("GUARD_BROKEN", {"playerId": pid, "nodeId": target, "oldDefense": old_defense})

            # Check bounty reward
            bounty = self._find_bounty(target)
            if bounty and bounty.get("active") and not bounty.get("completed"):
                attacking_score = p.task_score_base + p.bounty_score
                # Check if attacker's score is lower
                defender_score = op.task_score_base + op.bounty_score
                if bounty_eligible and attacking_score < defender_score:
                    reward = bounty.get("rewardScore", 0)
                    p.bounty_score += reward
                    bounty["completed"] = True
                    bounty["active"] = False
                    bounty["winnerPlayerId"] = pid
                    self._add_event("BOUNTY_COMPLETED", {"playerId": pid, "nodeId": target, "score": reward})

            self._add_action_result(pid, "BREAK_GUARD", True, "ACCEPTED", success=True)
        else:
            # Fail: reduce defense
            guard.defense = max(0, guard_defense - break_power)
            guard.opponent_fail_count += 1
            p.status = "RESTING"
            p.current_process = {"type": "REST", "framesLeft": 5, "totalFrames": 5}
            self._add_event("GUARD_ATTACK_FAILED", {"playerId": pid, "nodeId": target, "damage": break_power, "remainingDefense": guard.defense})
            self._add_action_result(pid, "BREAK_GUARD", False, "BREAK_FAILED", success=False)

        p.last_action = "BREAK_GUARD"
        p.last_action_result = "ACCEPTED" if break_power >= guard_defense else "ACTION_REJECTED"

    def _do_forced_pass(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        target = act.get("targetNodeId")

        if not target:
            self._add_action_result(pid, "FORCED_PASS", False, "MISSING_TARGET")
            return

        # Check adjacency
        edge = self._find_edge(p.station, target)
        if edge is None:
            self._add_action_result(pid, "FORCED_PASS", False, "TARGET_NOT_REACHABLE")
            return

        # Check repeat
        if p.last_forced_pass_node == target:
            self._add_action_result(pid, "FORCED_PASS", False, "FORCED_PASS_REPEAT")
            return

        # Check if target has obstacle or enemy guard
        obs = self.obstacles.get(target)
        has_obs = obs is not None and not obs.cleared
        has_enemy_guard = False
        guard_defense = 0
        guard_owner_team = ""
        for opid, op in self.players.items():
            if opid == pid:
                continue
            if target in op.guards and op.guards[target].defense > 0:
                has_enemy_guard = True
                guard_defense = op.guards[target].defense
                guard_owner_team = op.team_id
                break

        if not has_obs and not has_enemy_guard:
            self._add_action_result(pid, "FORCED_PASS", False, "NO_BLOCKER")
            return

        # Calculate time tax
        node_info = C.NODE_INFO.get(target, {})
        if has_enemy_guard:
            ntype = node_info.get("type", "")
            if ntype == "KEY_PASS":
                time_tax = min(50, 15 + guard_defense * 5)
            elif ntype == "GATE":
                time_tax = min(32, 12 + guard_defense * 5)
            elif has_obs:
                time_tax = min(28, 8 + guard_defense * 5)
            else:
                time_tax = min(40, 10 + guard_defense * 5)
        elif has_obs:
            time_tax = 8
        else:
            time_tax = 0

        # Route frames
        coeff = C.ROUTE_COEFFICIENT.get(edge["type"], 1380)
        speed = self._effective_speed(p)
        weather_mult = self._weather_move_mult(edge["type"])
        route_frames = _edge_frames(edge["dist"], coeff, speed, weather_mult)

        total_frames = route_frames + time_tax

        if has_enemy_guard:
            # Create PASS contest window
            contest_id = f"C_{self.frame}_{self._contest_counter}"
            self._contest_counter += 1
            c = ContestWindow(
                contest_id=contest_id,
                contest_type="PASS",
                target_node=target,
                red_player_id=self._team_to_player_id("RED"),
                blue_player_id=self._team_to_player_id("BLUE"),
                initiator_player_id=pid,
                round_index=1,
                total_rounds=3,
                deadline_round=self.frame + 1,
                initial_time_tax=time_tax,
                initial_block_type="GUARD",
                initial_guard_owner_team=guard_owner_team,
                initial_guard_defense=guard_defense,
                initial_has_obstacle=has_obs,
                initial_obstacle_tax=8 if has_obs else 0,
            )
            self.contests.append(c)

            p.status = "CONTESTING"
            self._add_event("CONTEST_CREATED", {"contestId": contest_id, "type": "PASS", "target": target})
            self._add_action_result(pid, "FORCED_PASS", True, "PASS_CONTEST_CREATED")
        else:
            # Obstacle only: direct forced pass
            p.status = "FORCED_PASSING"
            p.current_process = {
                "type": "FORCED_PASS",
                "target": target,
                "framesLeft": total_frames,
                "totalFrames": total_frames,
                "routeFrames": route_frames,
                "timeTax": time_tax,
            }
            p.target_station = target
            p.route_edge = edge["id"]
            p.move_accumulated = 0
            self._add_action_result(pid, "FORCED_PASS", True, "ACCEPTED")

        p.last_action = "FORCED_PASS"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_clear(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        target = act.get("targetNodeId")

        if not target:
            self._add_action_result(pid, "CLEAR", False, "MISSING_TARGET")
            return

        obs = self.obstacles.get(target)
        if obs is None or obs.cleared:
            self._add_action_result(pid, "CLEAR", False, "OBSTACLE_NOT_FOUND")
            return

        # Check proximity
        if p.station != target and target not in self._neighbors(p.station):
            self._add_action_result(pid, "CLEAR", False, "TARGET_NOT_REACHABLE")
            return

        if p.good_fruit < 1:
            self._add_action_result(pid, "CLEAR", False, "RESOURCE_NOT_ENOUGH")
            return

        clear_frames = 6
        reduced_frames, _ = self._apply_scout_reduction(p, target, clear_frames)

        p.status = "PROCESSING"
        p.frozen_good_fruit += 1
        p.current_process = {
            "type": "CLEAR_OBSTACLE",
            "target": target,
            "framesLeft": reduced_frames,
            "totalFrames": reduced_frames,
        }
        p.processing_frame_start = self.frame

        self._add_action_result(pid, "CLEAR", True, "ACCEPTED")
        p.last_action = "CLEAR"
        p.last_action_accepted = True
        p.last_action_result = "ACCEPTED"

    def _do_rush_speed(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        if self.phase not in ("RUSH", "BANQUET", "ENDGAME"):
            self._add_action_result(pid, "RUSH_SPEED", False, "NOT_IN_RUSH")
            return
        if p.rush_tactic_used > 0:
            self._add_action_result(pid, "RUSH_SPEED", False, "RUSH_TACTIC_ALREADY_USED")
            return
        if p.good_fruit < 2:
            self._add_action_result(pid, "RUSH_SPEED", False, "RESOURCE_NOT_ENOUGH")
            return
        if p.has_buff("FAST_HORSE") or p.has_buff("SHORT_HORSE"):
            self._add_action_result(pid, "RUSH_SPEED", False, "HORSE_BUFF_CONFLICT")
            return

        p.good_fruit -= 2
        p.rush_tactic_used = 1
        p.buffs["RUSH_SPEED"] = 15
        self._add_action_result(pid, "RUSH_SPEED", True, "ACCEPTED")
        self._add_event("RUSH_TACTIC_USED", {"playerId": pid, "tactic": "RUSH_SPEED"})

    def _do_rush_protect(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        if self.phase not in ("RUSH", "BANQUET", "ENDGAME"):
            self._add_action_result(pid, "RUSH_PROTECT", False, "NOT_IN_RUSH")
            return
        if p.rush_tactic_used > 0:
            self._add_action_result(pid, "RUSH_PROTECT", False, "RUSH_TACTIC_ALREADY_USED")
            return
        if p.status not in ("IDLE", "WAITING"):
            self._add_action_result(pid, "RUSH_PROTECT", False, f"STATE_{p.status}_FORBIDDEN")
            return

        p.rush_tactic_used = 1
        p.buffs["RUSH_PROTECT"] = 30
        self._add_action_result(pid, "RUSH_PROTECT", True, "ACCEPTED")
        self._add_event("RUSH_TACTIC_USED", {"playerId": pid, "tactic": "RUSH_PROTECT"})

    # ── Process window cards ──

    def _deduct_window_card_cost(self, pid: str, card: str) -> bool:
        """Deduct resources for a window card. Returns False if cost cannot be paid (card becomes ABSTAIN)."""
        p = self.players[pid]
        if card == "ABSTAIN":
            return True
        if card == "YAN_DIE":
            if p.resources.get("PASS_TOKEN", 0) > 0:
                p.resources["PASS_TOKEN"] -= 1
                return True
            if p.resources.get("OFFICIAL_PERMIT", 0) > 0:
                p.resources["OFFICIAL_PERMIT"] -= 1
                return True
            return False
        if card == "QIANG_XING":
            if p.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
                return True
            if p.resources.get("FAST_HORSE", 0) > 0:
                p.resources["FAST_HORSE"] -= 1
                return True
            if p.resources.get("SHORT_HORSE", 0) > 0:
                p.resources["SHORT_HORSE"] -= 1
                return True
            return False
        if card == "XIAN_GONG":
            if p.freshness < 80 or p.good_fruit < 1:
                return False
            p.good_fruit -= 1
            return True
        if card == "BING_ZHENG":
            if p.guard_points <= 0:
                return False
            p.guard_points -= 1
            return True
        return True

    def _process_windows(self, frame: int,
                         window_actions: dict[str, dict[str, Any] | None]) -> None:
        for contest in list(self.contests):
            if contest.resolved or contest.suppressed:
                continue
            if contest.deadline_round > frame:
                continue

            # Determine who is RED and BLUE
            red_pid = str(contest.red_player_id) if contest.red_player_id else ""
            blue_pid = str(contest.blue_player_id) if contest.blue_player_id else ""

            red_card = None
            blue_card = None

            if red_pid in window_actions and window_actions[red_pid]:
                wact = window_actions[red_pid]
                if wact.get("contestId") == contest.contest_id:
                    red_card = wact.get("card", "ABSTAIN")

            if blue_pid in window_actions and window_actions[blue_pid]:
                wact = window_actions[blue_pid]
                if wact.get("contestId") == contest.contest_id:
                    blue_card = wact.get("card", "ABSTAIN")

            red_card = red_card or "ABSTAIN"
            blue_card = blue_card or "ABSTAIN"

            contest.red_card = red_card
            contest.blue_card = blue_card

            # Deduct window card costs; if insufficient, downgrade to ABSTAIN
            if not self._deduct_window_card_cost(red_pid, red_card) and red_pid and red_card != "ABSTAIN":
                red_card = "ABSTAIN"
                contest.red_card = "ABSTAIN"
                self._add_event("WINDOW_CARD_COST_FAILED", {"playerId": red_pid, "contestId": contest.contest_id, "card": red_card})
            if not self._deduct_window_card_cost(blue_pid, blue_card) and blue_pid and blue_card != "ABSTAIN":
                blue_card = "ABSTAIN"
                contest.blue_card = "ABSTAIN"
                self._add_event("WINDOW_CARD_COST_FAILED", {"playerId": blue_pid, "contestId": contest.contest_id, "card": blue_card})

            # Resolve round
            result = C.WINDOW_MATRIX.get(red_card, {}).get(blue_card, "DRAW")
            if result == "WIN":
                contest.red_point += 1
            elif result == "LOSE":
                contest.blue_point += 1
            else:
                pass  # DRAW: no points

            # Advance round
            contest.round_index += 1
            contest.deadline_round = frame + 1  # next round due next frame

            if contest.round_index > contest.total_rounds:
                # Final resolution
                if contest.red_point > contest.blue_point:
                    contest.winner_team = "RED"
                    contest.resolved = True
                elif contest.blue_point > contest.red_point:
                    contest.winner_team = "BLUE"
                    contest.resolved = True
                else:
                    # Draw
                    contest.winner_team = "DRAW"
                    contest.resolved = True
                    self._handle_draw(frame, contest)

                contest.round_index = contest.total_rounds + 1
                self._add_event("CONTEST_RESOLVED", {
                    "contestId": contest.contest_id,
                    "winner": contest.winner_team,
                    "redCards": red_card,
                    "blueCards": blue_card,
                })

                # Apply contest result
                self._apply_contest_result(contest)

    def _handle_draw(self, frame: int, contest: ContestWindow) -> None:
        # Track draw count
        obj_key = contest.object_key or contest.contest_id
        current_draws = self.draw_suppress.get(obj_key, 0)

        if contest.contest_type == "GATE":
            max_draws = 2
            cooldown = 6
        else:
            max_draws = 2
            cooldown = 18

        if current_draws >= max_draws:
            contest.suppressed = True
            contest.suppress_until_round = frame + cooldown
            contest.object_key = obj_key
            self.draw_suppress[obj_key] = frame + cooldown
            self._add_event("CONTEST_SUPPRESSED", {"contestId": contest.contest_id, "until": frame + cooldown})
        else:
            self.draw_suppress[obj_key] = current_draws + 1

    def _apply_contest_result(self, contest: ContestWindow) -> None:
        """Apply the result of a resolved contest."""
        if contest.winner_team in ("DRAW",):
            # Put both players into RESTING (except PASS defender)
            for pid in [self.player1_id, self.player2_id]:
                p = self.players[pid]
                if contest.contest_type == "PASS" and str(pid) != str(contest.initiator_player_id):
                    # Defender does not pay the failed-pass rest tax, but also
                    # must not remain in the contest state.
                    if p.status == "CONTESTING":
                        p.status = "IDLE"
                        p.current_process = None
                    continue
                if p.status in ("CONTESTING", "IDLE", "WAITING"):
                    p.status = "RESTING"
                    p.current_process = {"type": "REST", "framesLeft": 3, "totalFrames": 3}

        elif contest.contest_type == "PASS":
            # Determine initiator's team
            initiator_team = self.team_map.get(str(contest.initiator_player_id), "")
            if contest.winner_team == initiator_team:
                # Initiator wins: start forced pass
                pid = str(contest.initiator_player_id)
                p = self.players[pid]
                p.status = "FORCED_PASSING"
                total = contest.initial_time_tax + 10
                p.current_process = {
                    "type": "FORCED_PASS",
                    "target": contest.target_node,
                    "framesLeft": total,
                    "totalFrames": total,
                }
                p.target_station = contest.target_node
                edge = self._find_edge(p.station, contest.target_node)
                if edge:
                    p.route_edge = edge["id"]
                    p.move_accumulated = 0
            else:
                # Defender wins or draw -> initiator rests
                pid = str(contest.initiator_player_id)
                p = self.players[pid]
                p.status = "RESTING"
                rest_frames = max(3, min(8, contest.initial_time_tax // 4 + 3))
                p.current_process = {"type": "REST", "framesLeft": rest_frames, "totalFrames": rest_frames}
            # In PASS contests, ensure defender is never stuck CONTESTING
            defender_pid = self._team_to_player_id("RED" if initiator_team == "BLUE" else "BLUE")
            if defender_pid:
                dp = self.players.get(str(defender_pid))
                if dp and dp.status == "CONTESTING":
                    dp.status = "IDLE"
                    dp.current_process = None

        else:
            # Non-DRAW, non-PASS contest (RESOURCE, TASK, GATE, DOCK, OBSTACLE).
            # Restore BOTH players from CONTESTING back to IDLE after resolution.
            for pid in [self.player1_id, self.player2_id]:
                p = self.players[pid]
                if p.status == "CONTESTING":
                    p.status = "IDLE"
                    p.current_process = None

            if contest.contest_type == "RESOURCE":
                winner_team = contest.winner_team
                if winner_team and contest.target_node and contest.resource_type:
                    for pid, p in self.players.items():
                        if p.team_id == winner_team:
                            stock = self.resource_stock.get(contest.target_node, {})
                            if contest.resource_type in stock and stock[contest.resource_type] > 0:
                                stock[contest.resource_type] -= 1
                                p.resources[contest.resource_type] = p.resources.get(contest.resource_type, 0) + 1
                                self._add_event("RESOURCE_CLAIM", {
                                    "playerId": pid,
                                    "nodeId": contest.target_node,
                                    "resourceType": contest.resource_type,
                                })
                            break

            elif contest.contest_type == "TASK":
                winner_team = contest.winner_team
                if winner_team and contest.task_id:
                    task = self._find_task(contest.task_id)
                    if task and not task.completed:
                        for pid, p in self.players.items():
                            if p.team_id == winner_team:
                                task.owner_player_id = pid
                                p.status = "PROCESSING"
                                p.current_process = {
                                    "type": "CLAIM_TASK",
                                    "taskId": task.task_id,
                                    "template": task.template,
                                    "target": task.target,
                                    "framesLeft": task.process_frames,
                                    "totalFrames": task.process_frames,
                                    "score": task.score,
                                }
                                break

    # ── Squad actions ──

    def _process_squad_action(self, pid: str, act: dict[str, Any]) -> None:
        p = self.players[pid]
        atype = act.get("action", "")
        target = act.get("targetNodeId")

        if not target:
            return

        if p.squad_available <= 0:
            self._add_action_result(pid, atype, False, "SQUAD_NOT_AVAILABLE")
            return

        # Calculate arrival frame
        dx = abs(C.NODE_INFO.get(p.station, {}).get("x", 0) - C.NODE_INFO.get(target, {}).get("x", 0))
        dy = abs(C.NODE_INFO.get(p.station, {}).get("y", 0) - C.NODE_INFO.get(target, {}).get("y", 0))
        d = max(dx, dy)
        base_delay = max(3, min(15, (d + 2) // 3))

        # Weather effect on scout
        if atype == "SQUAD_SCOUT" and self.current_weather and self.current_weather.weather_type == "MOUNTAIN_FOG":
            base_delay += 2

        arrival_frame = self.frame + base_delay

        if atype == "SQUAD_SCOUT":
            p.squad_available -= 1
            p.squad_in_flight += 1
            p.last_action = atype
            p.last_action_accepted = True
            # Schedule arrival
            self._schedule_squad_arrival(pid, atype, target, arrival_frame)
            self._add_action_result(pid, atype, True, "ACCEPTED")

        elif atype == "SQUAD_CLEAR":
            obs = self.obstacles.get(target)
            if obs is None or obs.cleared:
                self._add_action_result(pid, atype, False, "OBSTACLE_NOT_FOUND")
                return
            # Guard: squad clears on arrival
            p.squad_available -= 2
            p.squad_in_flight += 1
            self._schedule_squad_arrival(pid, atype, target, arrival_frame)
            self._add_action_result(pid, atype, True, "ACCEPTED")

        elif atype == "SQUAD_REINFORCE":
            # Check own guard at target
            if target not in p.guards or p.guards[target].defense <= 0:
                self._add_action_result(pid, atype, False, "NO_OWN_GUARD")
                return
            p.squad_available -= 2
            p.squad_in_flight += 1
            self._schedule_squad_arrival(pid, atype, target, arrival_frame,
                                         guard_info={"defense_boost": 2, "cap": p.guards[target].cap})
            self._add_action_result(pid, atype, True, "ACCEPTED")

        elif atype == "SQUAD_WEAKEN":
            # Check enemy guard at target
            enemy_found = False
            for opid, op in self.players.items():
                if opid == pid:
                    continue
                if target in op.guards and op.guards[target].defense > 0:
                    enemy_found = True
                    break
            if not enemy_found:
                self._add_action_result(pid, atype, False, "NO_ENEMY_GUARD")
                return
            p.squad_available -= 2
            p.squad_in_flight += 1
            self._schedule_squad_arrival(pid, atype, target, arrival_frame)
            self._add_action_result(pid, atype, True, "ACCEPTED")

        else:
            self._add_action_result(pid, atype, False, "INVALID_ACTION_TYPE")

    def _schedule_squad_arrival(self, pid: str, atype: str, target: str,
                                arrival_frame: int, guard_info: dict[str, Any] | None = None) -> None:
        if not hasattr(self, '_pending_squad_arrivals'):
            self._pending_squad_arrivals: dict[int, list[dict]] = {}
        entry = {
            "playerId": pid,
            "actionType": atype,
            "target": target,
            "frame": arrival_frame,
        }
        if guard_info:
            entry["guardInfo"] = guard_info
        self._pending_squad_arrivals.setdefault(arrival_frame, []).append(entry)

    def _process_arriving_squads(self, frame: int) -> None:
        if not hasattr(self, '_pending_squad_arrivals'):
            return
        arrivals = self._pending_squad_arrivals.pop(frame, [])
        for entry in arrivals:
            pid = entry["playerId"]
            p = self.players[pid]
            atype = entry["actionType"]
            target = entry["target"]
            p.squad_in_flight = max(0, p.squad_in_flight - 1)

            if atype == "SQUAD_SCOUT":
                if target not in self.scout_markers:
                    self.scout_markers[target] = []
                self.scout_markers[target].append(ScoutMarker(
                    team_id=p.team_id,
                    start_frame=frame,
                    end_frame=frame + 45,
                ))
                self._add_event("SCOUT_MARKER_ADDED", {"playerId": pid, "targetNode": target, "source": "SQUAD_SCOUT"})

            elif atype == "SQUAD_CLEAR":
                obs = self.obstacles.get(target)
                if obs and not obs.cleared:
                    obs.cleared = True
                    obs.clear_frame = frame
                    obs.clear_team = pid
                    self.stations[target]["hasObstacle"] = False
                    self._add_event("OBSTACLE_CLEARED", {"playerId": pid, "nodeId": target, "source": "SQUAD_CLEAR"})

            elif atype == "SQUAD_REINFORCE":
                if target in p.guards and p.guards[target].defense > 0:
                    boost = entry.get("guardInfo", {}).get("defense_boost", 2)
                    cap = entry.get("guardInfo", {}).get("cap", 6)
                    old_def = p.guards[target].defense
                    p.guards[target].defense = min(cap, old_def + boost)
                    self._add_event("GUARD_REINFORCED", {"playerId": pid, "nodeId": target, "oldDefense": old_def, "newDefense": p.guards[target].defense})

            elif atype == "SQUAD_WEAKEN":
                for opid, op in self.players.items():
                    if opid == pid:
                        continue
                    if target in op.guards and op.guards[target].defense > 0:
                        old_def = op.guards[target].defense
                        op.guards[target].defense = max(0, old_def - 2)
                        if op.guards[target].defense == 0:
                            self._add_event("GUARD_COLLAPSED", {"nodeId": target})
                        self._add_event("GUARD_WEAKENED", {"playerId": pid, "nodeId": target, "oldDefense": old_def, "newDefense": op.guards[target].defense})
                        break

    # ── State advancement ──

    def _advance_states(self, frame: int) -> None:
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]

            # Decrement process ticks (once)
            if p.current_process:
                p.current_process["framesLeft"] = max(0, p.current_process["framesLeft"] - 1)

            # Check completion in priority order
            if p.current_process and p.current_process.get("framesLeft", 0) <= 0:
                ctype = p.current_process.get("type", "")
                if ctype == "VERIFY":
                    self._complete_verify(pid)
                    continue
                if ctype == "FORCED_PASS":
                    self._complete_forced_pass(pid)
                    continue
                if ctype == "REST":
                    p.status = "IDLE"
                    p.current_process = None
                    self._add_event("REST_COMPLETE", {"playerId": pid})
                    continue
                self._complete_process(pid)

            # Advance movement
            if p.status == "MOVING" and p.route_edge:
                self._advance_move(pid, frame)

        # Process upcoming contests
        self._advance_contests(frame)

    def _advance_move(self, pid: str, frame: int) -> None:
        p = self.players[pid]
        edge = self._edge_by_id(p.route_edge)
        if not edge:
            p.status = "IDLE"
            return

        speed = self._effective_speed(p)
        weather_mult = self._weather_move_mult(edge["type"])
        coeff = C.ROUTE_COEFFICIENT.get(edge["type"], 1380)
        # Residual clearance tax adds extra wait as move units (1000 per frame)
        required = edge["dist"] * coeff + p.additional_wait_frames * 1000
        per_frame = (speed * 1000) // max(weather_mult, 1)

        p.move_accumulated += per_frame

        if p.move_accumulated >= required:
            if self._movement_arrival_blocked_by_guard(pid, p.target_station):
                p.move_accumulated = required
                return
            # Arrive at target
            arrived_node = p.target_station
            self._add_event("NODE_ENTER", {"playerId": pid, "nodeId": arrived_node, "fromNode": p.station})
            p.station = arrived_node
            p.status = "IDLE"
            p.target_station = None
            p.route_edge = None
            p.route_type = None
            p.move_accumulated = 0
            p.additional_wait_frames = 0
            # Reset fixed-process-completed flag on arrival at any station
            p.fixed_process_completed_here = False

    def _movement_arrival_blocked_by_guard(self, pid: str, target: str | None) -> bool:
        if not target:
            return False
        p = self.players[pid]
        for opid, op in self.players.items():
            if opid == pid:
                continue
            guard = op.guards.get(target)
            if guard is None or guard.defense <= 0 or guard.owner_team == p.team_id:
                continue
            self._add_event(
                "MOVE_BLOCKED_BY_GUARD",
                {
                    "playerId": pid,
                    "targetNodeId": target,
                    "nodeId": target,
                    "ownerTeamId": guard.owner_team,
                    "defense": guard.defense,
                    "whileMoving": True,
                },
            )
            self._add_action_result(
                pid,
                "MOVE",
                False,
                "MOVE_BLOCKED_BY_GUARD",
                error=f"Enemy guard at {target} while MOVING",
                targetNodeId=target,
                nodeId=target,
                whileMoving=True,
            )
            p.last_action = "MOVE"
            p.last_action_accepted = False
            p.last_action_result = "ACTION_REJECTED"
            p.last_action_error = "MOVE_BLOCKED_BY_GUARD"
            return True
        return False

    def _complete_forced_pass(self, pid: str) -> None:
        p = self.players[pid]
        target = p.current_process.get("target", "")
        self._add_event("NODE_ENTER", {"playerId": pid, "nodeId": target, "fromNode": p.station, "forced": True})
        p.station = target
        p.status = "IDLE"
        p.last_forced_pass_node = target
        p.current_process = None
        p.target_station = None
        p.route_edge = None
        # Entering via forced pass counts as a new visit — may need to redo PROCESS
        p.fixed_process_completed_here = False

    def _complete_verify(self, pid: str) -> None:
        p = self.players[pid]
        p.verified = True
        p.status = "IDLE"
        p.current_process = None
        self._add_event("VERIFY_COMPLETE", {"playerId": pid})

    def _complete_process(self, pid: str) -> None:
        p = self.players[pid]
        cptype = p.current_process.get("type", "")
        target = p.current_process.get("target", "")

        if cptype == "TRANSFER" or cptype in ("BOARD", "WATER_TRANSFER", "PASS_TRANSFER", "PALACE_TRANSFER"):
            # Fixed process completed
            p.fixed_process_completed_here = True
            self._add_event("PROCESS_COMPLETE", {"playerId": pid, "nodeId": target, "processType": cptype})
            p.status = "IDLE"

        elif cptype == "CLAIM_RESOURCE":
            rtype = p.current_process.get("resourceType", "")
            stock = self.resource_stock.get(target, {})
            if rtype in stock and stock[rtype] > 0:
                stock[rtype] -= 1
                p.resources[rtype] = p.resources.get(rtype, 0) + 1
                self._add_event("RESOURCE_CLAIM", {"playerId": pid, "nodeId": target, "resourceType": rtype})
            p.status = "IDLE"

        elif cptype == "CLAIM_TASK":
            task_id = p.current_process.get("taskId", "")
            task = self._find_task(task_id)
            if task and not task.completed and not task.failed:
                task.completed = True
                task.active = False
                p.task_score_base += task.score
                self._add_event("TASK_COMPLETE", {"playerId": pid, "taskId": task_id, "score": task.score})
            p.status = "IDLE"

        elif cptype == "CLEAR_OBSTACLE":
            # Consume frozen good fruit
            if p.frozen_good_fruit > 0:
                p.frozen_good_fruit -= 1
                p.good_fruit -= 1
            obs = self.obstacles.get(target)
            if obs and not obs.cleared:
                obs.cleared = True
                obs.clear_frame = self.frame
                obs.clear_team = pid
                self.stations[target]["hasObstacle"] = False
                self._add_event("OBSTACLE_CLEARED", {"playerId": pid, "nodeId": target, "source": "CLEAR"})
            p.status = "IDLE"

        elif cptype == "SET_GUARD":
            defense = p.current_process.get("defense", 2)
            cap = p.current_process.get("cap", 6)
            base_cost = p.current_process.get("baseCost", 0)
            extra_gf = p.current_process.get("extraGoodFruit", 0)
            total_cost = base_cost + extra_gf
            # Update frozen fruit
            if total_cost > 0 and p.frozen_good_fruit >= total_cost:
                p.frozen_good_fruit -= total_cost
                p.good_fruit -= total_cost
            node_info = C.NODE_INFO.get(target, {})
            is_key = node_info.get("type") == "KEY_PASS"
            p.guards[target] = GuardState(
                owner_team=p.team_id,
                defense=defense,
                cap=cap,
                completed_frame=self.frame,
                last_wind_frame=self.frame,
                wind_interval=C.GUARD_KEY_PASS_EXTRA_FIRST_WIND if is_key and defense >= 4 else C.GUARD_WIND_INTERVAL,
                is_key_pass=is_key,
            )
            self._add_event("GUARD_SET", {"playerId": pid, "nodeId": target, "defense": defense})
            p.status = "IDLE"

        else:
            p.status = "IDLE"

        p.current_process = None

    # ── Scout marker reduction ──

    def _apply_scout_reduction(self, player: Player, node_id: str, base_frames: int) -> tuple[int, bool]:
        """Apply scout marker time reduction if a valid marker exists.

        Returns (reduced_frames, was_reduced).  If a valid marker is found,
        frames are reduced by 3, minimum 2, and the marker is marked used.
        """
        markers = self.scout_markers.get(node_id, [])
        for marker in markers:
            if (marker.team_id == player.team_id
                    and marker.end_frame >= self.frame
                    and not marker.used):
                marker.used = True
                reduced = max(2, base_frames - 3)
                self._add_event("SCOUT_MARKER_USED", {
                    "playerId": player.player_id,
                    "nodeId": node_id,
                    "baseFrames": base_frames,
                    "reducedFrames": reduced,
                })
                return reduced, True
        return base_frames, False

    def _advance_contests(self, frame: int) -> None:
        # Remove resolved contests from previous rounds
        self.contests = [
            c for c in self.contests
            if not c.resolved and not c.suppressed
        ]

        # Handle suppression expiry
        expired_keys = [k for k, v in self.draw_suppress.items() if v < frame]
        for k in expired_keys:
            self.draw_suppress.pop(k, None)

    # ── Freshness ──

    def _apply_freshness(self, frame: int) -> None:
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]
            if p.delivered or p.retired:
                continue

            kind = _freshness_kind(p.status, p.route_type)
            base_decay = C.FRESHNESS_DECAY.get(kind, 0.05)

            # Weather multiplier
            weather_mult = 1.0
            if self.current_weather:
                if self.current_weather.weather_type == "HOT":
                    weather_mult *= C.HOT_FRESHNESS_MULTIPLIER
                elif self.current_weather.weather_type == "HEAVY_RAIN":
                    # Only if on water route or processing at water node
                    if kind == "WATER" or (p.station in ("S04", "S05")):
                        weather_mult *= C.HEAVY_RAIN_FRESHNESS_MULTIPLIER

            # Buff multiplier
            buff_mult = 1.0
            if p.buffs.get("RUSH_SPEED", 0) > 0:
                buff_mult *= C.RUSH_SPEED_FRESHNESS_MULTIPLIER
            if p.buffs.get("RUSH_PROTECT", 0) > 0:
                buff_mult *= C.RUSH_PROTECT_FRESHNESS_MULTIPLIER

            decay = base_decay * weather_mult * buff_mult

            p.freshness = max(0.0, p.freshness - decay)

    def _check_bad_fruit_conversion(self, pid: str) -> None:
        p = self.players[pid]
        if p.delivered or p.retired:
            return

        if p.freshness <= 0:
            # All remaining good fruit is lost
            if p.good_fruit > 0:
                self._add_event("FRUIT_SPOILED", {"playerId": pid, "count": p.good_fruit, "reason": "freshness_zero"})
                p.good_fruit = 0
            return

        for threshold in sorted(C.GOOD_FRUIT_BAD_THRESHOLDS, reverse=True):
            if p.freshness < threshold and threshold not in p.last_triggered_bad:
                if p.good_fruit <= 0:
                    continue
                p.last_triggered_bad.add(threshold)
                # Convert 1 good fruit to bad
                if p.good_fruit > 0:
                    p.good_fruit -= 1
                    p.bad_fruit += 1
                elif p.frozen_good_fruit > 0:
                    p.frozen_good_fruit -= 1
                    p.bad_fruit += 1
                    # Processing fails
                    if p.current_process and p.frozen_good_fruit > 0:
                        p.current_process = None
                        p.status = "COST_BANKRUPT"

                self._add_event("FRUIT_TURNED_BAD", {"playerId": pid, "threshold": threshold})
                break  # One conversion per frame

    # ── Weather ──

    def _update_weather(self, frame: int) -> None:
        self.current_weather = None
        for we in self.weather_events:
            if we.start_frame <= frame <= we.end_frame:
                self.current_weather = we
                break

    def _weather_move_mult(self, route_type: str) -> int:
        if not self.current_weather:
            return 1000
        if self.current_weather.weather_type == "HEAVY_RAIN" and route_type == "WATER":
            return 1350
        if self.current_weather.weather_type == "MOUNTAIN_FOG" and route_type == "MOUNTAIN":
            return 1100
        return 1000

    # ── Winds (guard decay) ──

    def _wind_guards(self, frame: int) -> None:
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]
            for nid, guard in list(p.guards.items()):
                if guard.defense <= 0:
                    continue
                if frame - guard.last_wind_frame >= guard.wind_interval:
                    guard.defense = max(0, guard.defense - 1)
                    guard.last_wind_frame = frame
                    guard.wind_interval = C.GUARD_WIND_INTERVAL  # reset to standard after first
                    if guard.defense <= 0:
                        self._add_event("GUARD_COLLAPSED", {"nodeId": nid, "ownerTeam": guard.owner_team})

    # ── Bounties ──

    def _check_bounties(self, frame: int) -> None:
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]
            for nid, guard in list(p.guards.items()):
                if guard.defense <= 0:
                    continue
                elapsed = frame - guard.completed_frame

                # Trigger bounty conditions
                should_trigger = False
                bounty_type = "NORMAL_BOUNTY"
                reward = C.NORMAL_BOUNTY_SCORE

                # 30 consecutive frames
                if elapsed >= 30 and not guard.bounty_claimed and elapsed < 60:
                    should_trigger = True
                # 60 consecutive frames
                if elapsed >= 60 and not guard.bounty_claimed:
                    should_trigger = True
                    if guard.is_key_pass:
                        bounty_type = "KEY_BOUNTY"
                        reward = C.KEY_BOUNTY_SCORE
                # Opponent failures
                if guard.opponent_fail_count >= 2 and not guard.bounty_claimed:
                    should_trigger = True
                if guard.is_key_pass and guard.opponent_attack_count >= 3 and not guard.bounty_claimed:
                    should_trigger = True
                    bounty_type = "KEY_BOUNTY"
                    reward = C.KEY_BOUNTY_SCORE

                if should_trigger:
                    guard.bounty_claimed = True
                    bid = f"B_{nid}_{self._bounty_counter}"
                    self._bounty_counter += 1
                    self.bounties.append({
                        "id": bid,
                        "type": bounty_type,
                        "nodeId": nid,
                        "ownerTeam": guard.owner_team,
                        "rewardScore": reward,
                        "triggerRound": frame,
                        "active": True,
                        "completed": False,
                        "winnerPlayerId": 0,
                    })
                    self._add_event("BOUNTY_CREATED", {"bountyId": bid, "nodeId": nid, "score": reward})

    def _check_bounty_on_attack(self, pid: str, node: str, guard: GuardState) -> bool:
        """Check if this attack can earn the bounty."""
        for b in self.bounties:
            if b.get("nodeId") == node and b.get("active") and not b.get("completed"):
                return True
        return False

    def _find_bounty(self, node: str) -> dict | None:
        for b in self.bounties:
            if b.get("nodeId") == node and b.get("active") and not b.get("completed"):
                return b
        return None

    # ── Tasks ──

    def _refresh_tasks(self, frame: int) -> None:
        active_count = sum(1 for t in self.tasks if t.active and not t.completed and not t.failed)
        if active_count >= C.TASK_MAX_ON_MAP:
            return

        # Pick a random template
        template_id = self.rng.choice(list(C.TASK_TEMPLATES.keys()))
        score, process_frames, candidates = C.TASK_TEMPLATES[template_id]
        target = self.rng.choice(candidates)

        # Check obstacle for T04
        if template_id == "T04":
            obs = self.obstacles.get(target)
            if obs is None or obs.cleared:
                # Find an uncleared obstacle
                active_obs = [nid for nid, o in self.obstacles.items() if not o.cleared]
                if not active_obs:
                    return
                obs_node = self.rng.choice(active_obs)
                # Find a task candidate near this obstacle
                for tid, (s, pf, cand) in C.TASK_TEMPLATES.items():
                    if tid == "T04":
                        # Use obstacle's neighbors
                        target = obs_node
                        break

        task_id = f"T_{self._task_counter}"
        self._task_counter += 1

        expire = frame + C.TASK_EXPIRE_FRAMES + self.rng.randint(0, 20)

        task = TaskInstance(
            task_id=task_id,
            template=template_id,
            target=target,
            score=score,
            process_frames=process_frames,
            refresh_frame=frame,
            expire_frame=expire,
            requires_horse=(template_id == "T06"),
            clears_obstacle=(template_id == "T04"),
            obstacle_target=target if template_id == "T04" else None,
        )
        self.tasks.append(task)
        self._add_event("TASK_REFRESHED", {"taskId": task_id, "template": template_id, "target": target, "score": score})

    def _expire_tasks(self, frame: int) -> None:
        for t in self.tasks:
            if t.active and not t.completed and not t.failed:
                if t.expire_frame > 0 and frame > t.expire_frame:
                    t.active = False
                    t.failed = True
                    self._add_event("TASK_EXPIRED", {"taskId": t.task_id})

    # ── RUSH phase ──

    def _check_rush_trigger(self, frame: int) -> None:
        if self.phase != "NORMAL":
            return

        trigger = False

        # Frame 450: always trigger
        if frame >= 450:
            trigger = True
        # Frame 390-449: check conditions
        elif frame >= 390:
            for pid in [self.player1_id, self.player2_id]:
                p = self.players[pid]
                if p.delivered or p.retired:
                    continue
                # Condition 1: at S14
                if p.station == "S14":
                    trigger = True
                    break
                # Condition 2: not at S11/S12/S13, and distance to S14 <= 15
                if p.station not in ("S11", "S12", "S13"):
                    dist = self._route_distance(p.station, "S14")
                    if dist is not None and dist <= 15:
                        trigger = True
                        break
                # Condition 3: fastest route can reach S15 within 60 frames (estimated)
                if p.station is not None:
                    frames_to_gate = self._estimate_fastest_frames(p.station, "S14")
                    frames_to_term = self._estimate_fastest_frames("S14", "S15")
                    if frames_to_gate is not None and frames_to_term is not None:
                        if frames_to_gate + frames_to_term <= 60:
                            trigger = True
                            break

        if trigger:
            self.phase = "RUSH"
            self._add_event("RUSH_PHASE_STARTED", {"round": frame})

    def _estimate_fastest_frames(self, start: str, end: str) -> int | None:
        """Estimate fastest possible frames ignoring weather, obstacles, guards."""
        if start == end:
            return 0
        visited = {start}
        queue = [(start, 0)]
        while queue:
            node, cost = queue.pop(0)
            for neighbor in self._neighbors(node):
                if neighbor in visited:
                    continue
                edge = self._find_edge(node, neighbor)
                if edge:
                    coeff = C.ROUTE_COEFFICIENT.get(edge["type"], 1380)
                    frames = max(1, (edge["dist"] * coeff + 999) // 1000)
                    nc = cost + frames
                    if neighbor == end:
                        return nc
                    visited.add(neighbor)
                    queue.append((neighbor, nc))
        return None

    # ── Scoring ──

    def _update_scores(self, frame: int) -> None:
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]

            # Calculate task milestones
            milestones = 0
            for milestone_threshold, milestone_score in C.TASK_SCORE_MILESTONES:
                if p.task_score_base >= milestone_threshold:
                    milestones += milestone_score
            p.task_score = min(180, p.task_score_base + milestones)

            # Delivery score
            delivery_score = 0
            good_fruit_score = 0
            freshness_score = 0
            time_score = 0

            if p.delivered:
                # Delivery base
                task_factor = min(1.0, max(0.5, p.task_score_base / 90.0)) if p.task_score_base < 90 else 1.0
                delivery_score = min(240, 120 + int(p.task_score_base * 4 / 3))

                # Good fruit score
                good_fruit_score = int(p.good_fruit / 100 * 180)

                # Freshness score
                freshness_score = int(p.freshness / 100 * 180)

                # Time score
                if p.deliver_round > 0:
                    raw_time = int((600 - p.deliver_round) / 600 * 70)
                    time_score = int(raw_time * task_factor)

            # Penalty
            if p.illegal_action_count > 5:
                penalty = min(20, p.illegal_action_count - 5)
            else:
                penalty = 0
            post_deliver_penalty = min(30, p.post_deliver_penalty * 5)

            total_penalty = penalty + post_deliver_penalty

            # Bounty score
            bounty = p.bounty_score

            total = delivery_score + good_fruit_score + freshness_score + time_score + p.task_score + bounty - total_penalty
            total = max(0, total)

            p.total_score = total

        # Update preview
        for pid, p in self.players.items():
            self.score_preview[p.team_id] = p.total_score

    # ── End conditions ──

    def _check_end_conditions(self, frame: int) -> None:
        if self.ended:
            return

        # Both delivered
        if self.players[self.player1_id].delivered and self.players[self.player2_id].delivered:
            self.ended = True
            return

        # Max frames
        if self.frame >= C.MAX_FRAMES:
            self.ended = True
            return

        # Both retired
        if self.players[self.player1_id].retired and self.players[self.player2_id].retired:
            self.ended = True
            return

    def get_over_payload(self) -> dict[str, Any]:
        """Generate the over message."""
        p1 = self.players[self.player1_id]
        p2 = self.players[self.player2_id]

        # Determine winner
        winner = None
        result_type = "NORMAL"
        over_reason = "ALL_DELIVERED"

        if p1.total_score > p2.total_score:
            winner = self.player1_id
        elif p2.total_score > p1.total_score:
            winner = self.player2_id
        else:
            result_type = "DRAW"
            over_reason = "SCORE_TIE"

        if p1.retired and p2.retired:
            result_type = "DRAW"
            over_reason = "BOTH_RETIRED"
            winner = None
        elif p1.retired or p2.retired:
            result_type = "FORFEIT"
            over_reason = "SINGLE_RETIRED"
            winner = self.player2_id if p1.retired else self.player1_id

        if self.frame >= C.MAX_FRAMES:
            over_reason = "TIME_LIMIT"

        return {
            "msg_name": "over",
            "msg_data": {
                "matchId": self.match_id,
                "overRound": self.frame,
                "resultType": result_type,
                "overReason": over_reason,
                "winnerPlayerId": int(winner) if winner else None,
                "players": [
                    self._player_over_data(self.player1_id),
                    self._player_over_data(self.player2_id),
                ],
            },
        }

    def _player_over_data(self, pid: str) -> dict[str, Any]:
        p = self.players[pid]
        return {
            "playerId": int(pid) if pid.isdigit() else pid,
            "playerName": f"Player-{p.team_id}",
            "camp": 0 if self.team_map[pid] == "RED" else 1,
            "online": True,
            "delivered": p.delivered,
            "retired": p.retired,
            "deliverRound": p.deliver_round,
            "freshness": round(p.freshness, 2),
            "goodFruit": p.good_fruit,
            "badFruit": p.bad_fruit,
            "taskScore": p.task_score,
            "bountyScore": p.bounty_score,
            "totalScore": p.total_score,
            "penaltyScore": min(20, max(0, p.illegal_action_count - 5)) + min(30, p.post_deliver_penalty * 5),
            "scoreDetail": {
                "delivery": min(240, 120 + int(p.task_score_base * 4 / 3)) if p.delivered else 0,
                "goodFruit": int(p.good_fruit / 100 * 180) if p.delivered else 0,
                "freshness": int(p.freshness / 100 * 180) if p.delivered else 0,
                "time": self._time_score(p),
                "tasks": p.task_score,
                "bounty": p.bounty_score,
                "penalty": min(20, max(0, p.illegal_action_count - 5)) + min(30, p.post_deliver_penalty * 5),
                "total": p.total_score,
            },
        }

    def _time_score(self, p: Player) -> int:
        if not p.delivered or p.deliver_round <= 0:
            return 0
        task_factor = min(1.0, p.task_score_base / 90) if p.task_score_base < 90 else 1.0
        raw = int((600 - p.deliver_round) / 600 * 70)
        return int(raw * task_factor)

    # ── Helpers ──

    def _opponent_id(self, pid: str) -> str | None:
        for pp in [self.player1_id, self.player2_id]:
            if pp != pid:
                return pp
        return None

    def _find_edge(self, start: str, end: str) -> dict[str, Any] | None:
        for e in self.edges:
            if e["from"] == start and e["to"] == end:
                return e
            if e.get("bidirectional", True) and e["from"] == end and e["to"] == start:
                return {"id": e["id"], "from": e["to"], "to": e["from"],
                        "type": e["type"], "dist": e["dist"], "bidirectional": True}
        return None

    def _edge_by_id(self, eid: str | None) -> dict[str, Any] | None:
        if not eid:
            return None
        for e in self.edges:
            if e["id"] == eid:
                return e
        return None

    def _edge_start(self, eid: str, current_station: str) -> str | None:
        e = self._edge_by_id(eid)
        if not e:
            return None
        if e["from"] == current_station:
            return e["from"]
        if e["to"] == current_station:
            return e["to"]
        return None

    def _neighbors(self, node: str) -> list[str]:
        result = []
        for e in self.edges:
            if e["from"] == node:
                result.append(e["to"])
            elif e.get("bidirectional", True) and e["to"] == node:
                result.append(e["from"])
        return result

    def _route_distance(self, start: str, end: str) -> int | None:
        """BFS-based route distance calculation."""
        if start == end:
            return 0
        visited = {start}
        queue = [(start, 0)]
        while queue:
            node, dist = queue.pop(0)
            for neighbor in self._neighbors(node):
                if neighbor in visited:
                    continue
                edge = self._find_edge(node, neighbor)
                if edge:
                    nd = dist + edge["dist"]
                    if neighbor == end:
                        return nd
                    visited.add(neighbor)
                    queue.append((neighbor, nd))
        return None

    def _effective_speed(self, p: Player) -> int:
        if p.buffs.get("RUSH_SPEED", 0) > 0:
            return C.RUSH_SPEED_BOOST
        if p.buffs.get("FAST_HORSE", 0) > 0:
            return C.FAST_HORSE_SPEED
        if p.buffs.get("SHORT_HORSE", 0) > 0:
            return C.SHORT_HORSE_SPEED
        return C.BASE_SPEED

    def _has_buff(self, p: Player, *buff_types: str) -> bool:
        return any(b in p.buffs for b in buff_types)

    def _find_task(self, task_id: str) -> TaskInstance | None:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def _team_to_player_id(self, team: str) -> str | None:
        for pid, p in self.players.items():
            if p.team_id == team:
                return pid
        return None

    def _add_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append(GameEvent(type=event_type, round=self.frame, payload=payload))

    def _add_action_result(self, pid: str, action: str, accepted: bool, result: str,
                           error: str | None = None, success: bool | None = None,
                           **extra_fields: Any) -> None:
        entry: dict[str, Any] = {
            "round": self.frame,
            "playerId": int(pid) if pid.isdigit() else pid,
            "action": action,
            "accepted": accepted,
            "result": result,
        }
        # Always write all error fields for rejected actions so strategy can learn
        if not accepted:
            error_code = error or result
            entry["errorCode"] = error_code
            entry["code"] = result  # short canonical code
            entry["reason"] = error or result
            entry["message"] = error or result
        elif error:
            entry["errorCode"] = error
            entry["message"] = error
        if success is not None:
            entry["success"] = success
        entry.update({key: value for key, value in extra_fields.items() if value is not None})
        self.action_results.append(entry)

    def _count_illegal(self, pid: str) -> None:
        p = self.players[pid]
        p.illegal_action_count += 1

    def _reject_illegal_action(self, pid: str, action: str, result: str,
                               error: str | None = None) -> None:
        p = self.players[pid]
        self._add_action_result(pid, action, False, result, error=error)
        p.last_action = action
        p.last_action_accepted = False
        p.last_action_result = result
        p.last_action_error = error or result
        self._count_illegal(pid)

    def _advance_buffs(self) -> None:
        for pid in [self.player1_id, self.player2_id]:
            p = self.players[pid]
            expired = []
            for btype, remaining in p.buffs.items():
                if remaining > 0:
                    p.buffs[btype] = remaining - 1
                else:
                    expired.append(btype)
            for b in expired:
                del p.buffs[b]
