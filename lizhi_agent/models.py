from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConvoyStatus(str, Enum):
    IDLE = "IDLE"
    MOVING = "MOVING"
    WAITING = "WAITING"
    PROCESSING = "PROCESSING"
    CONTESTING = "CONTESTING"
    RESTING = "RESTING"
    FORCED_PASSING = "FORCED_PASSING"
    VERIFYING = "VERIFYING"
    COST_BANKRUPT = "COST_BANKRUPT"
    DELIVERED = "DELIVERED"
    RETIRED = "RETIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Station:
    id: str
    name: str = ""
    node_type: str = ""
    process_type: str | None = None
    process_round: int = 0
    has_obstacle: bool = False
    guard_owner: str | None = None
    guard_defense: int = 0
    can_window: bool = False
    resource_stock: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def has_enemy_guard(self, my_team: str | None) -> bool:
        return bool(self.guard_owner and self.guard_owner != my_team and self.guard_defense > 0)


@dataclass(frozen=True)
class RouteEdge:
    id: str
    start: str
    end: str
    route_type: str = "ROAD"
    distance: int = 1
    bidirectional: bool = True

    def other(self, station_id: str) -> str | None:
        if station_id == self.start:
            return self.end
        if self.bidirectional and station_id == self.end:
            return self.start
        return None


@dataclass(frozen=True)
class TaskInstance:
    id: str
    template: str
    target: str
    route_bucket: str = ""
    score: int = 0
    process_frames: int = 0
    refresh_frame: int = 0
    expire_frame: int = 0
    active: bool = True
    completed: bool = False
    failed: bool = False
    owner_player_id: int | str | None = None
    protection_player_id: int | str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.completed:
            return "COMPLETED"
        if self.failed:
            return "FAILED"
        if not self.active:
            return "INACTIVE"
        return "ACTIVE"

    @property
    def is_valuable(self) -> bool:
        return self.score >= 30 or self.template in {"T01", "T02", "T04", "T06", "T08", "T11"}

    def available_for(self, player_id: str) -> bool:
        if self.status != "ACTIVE":
            return False
        if self.protection_player_id in (None, 0, "0", ""):
            return True
        return str(self.protection_player_id) == str(player_id)


@dataclass(frozen=True)
class ResourceStock:
    station: str
    resource_type: str
    amount: int = 0
    claim_frames: int = 2


@dataclass(frozen=True)
class WindowState:
    id: str
    window_type: str = "UNKNOWN"
    target: str | None = None
    resource_type: str | None = None
    task_id: str | None = None
    active: bool = False
    my_turn: bool = False
    round_index: int = 0
    red_point: int = 0
    blue_point: int = 0
    status: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeatherState:
    active_types: tuple[str, ...] = ()
    forecast_types: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlayerState:
    player_id: str
    team_id: str | None = None
    status: ConvoyStatus = ConvoyStatus.UNKNOWN
    station: str | None = None
    target: str | None = None
    route_edge_id: str | None = None
    route_type: str | None = None
    good_fruit: int = 100
    frozen_good_fruit: int = 0
    bad_fruit: int = 0
    freshness: float = 100.0
    task_score_base: int = 0
    bounty_score: int = 0
    total_score: int = 0
    delivered: bool = False
    verified: bool = False
    retired: bool = False
    resources: dict[str, int] = field(default_factory=dict)
    squad_available: int = 8
    squad_in_flight: int = 0
    guard_points: int = 4
    rush_tactic_used_count: int = 0
    buffs: tuple[str, ...] = ()
    current_process: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def has_resource(self, resource_type: str) -> bool:
        return self.resources.get(resource_type, 0) > 0

    def has_buff(self, *buff_types: str) -> bool:
        return any(buff in self.buffs for buff in buff_types)

    @property
    def can_start_station_action(self) -> bool:
        return self.status in {ConvoyStatus.IDLE, ConvoyStatus.WAITING, ConvoyStatus.UNKNOWN, ConvoyStatus.COST_BANKRUPT}


@dataclass(frozen=True)
class GameState:
    frame: int = 0
    max_frame: int = 600
    phase: str = "UNKNOWN"
    player_id: str = "player0"
    roles: dict[str, Any] = field(default_factory=dict)
    me: PlayerState = field(default_factory=lambda: PlayerState(player_id="player0"))
    opponent: PlayerState | None = None
    stations: dict[str, Station] = field(default_factory=dict)
    edges: list[RouteEdge] = field(default_factory=list)
    tasks: list[TaskInstance] = field(default_factory=list)
    resources: list[ResourceStock] = field(default_factory=list)
    windows: list[WindowState] = field(default_factory=list)
    weather: WeatherState | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    action_results: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def turns_left(self) -> int:
        return max(0, self.max_frame - self.frame)

    @property
    def start_node(self) -> str:
        return str(self.roles.get("startNodeId") or "S01")

    @property
    def gate_node(self) -> str:
        return str(self.roles.get("gateNodeId") or "S14")

    @property
    def terminal_node(self) -> str:
        terminals = self.roles.get("terminalNodeIds")
        if isinstance(terminals, list) and terminals:
            return str(terminals[0])
        return "S15"

    def active_window(self) -> WindowState | None:
        for window in self.windows:
            if window.active and window.my_turn and window.status != "SUPPRESSED":
                return window
        return None

    def station_resources(self, station_id: str | None) -> list[ResourceStock]:
        if station_id is None:
            return []
        return [r for r in self.resources if r.station == station_id and r.amount > 0]

    def station_tasks(self, station_id: str | None) -> list[TaskInstance]:
        if station_id is None:
            return []
        return [t for t in self.tasks if t.target == station_id and t.available_for(self.player_id)]

    def station(self, station_id: str | None) -> Station | None:
        return self.stations.get(station_id or "")

    def neighbors(self, station_id: str | None) -> list[str]:
        if station_id is None:
            return []
        result: list[str] = []
        for edge in self.edges:
            other = edge.other(station_id)
            if other is not None:
                result.append(other)
        return result


def _first_present(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_status(value: Any) -> ConvoyStatus:
    if isinstance(value, str):
        try:
            return ConvoyStatus(value)
        except ValueError:
            return ConvoyStatus.UNKNOWN
    return ConvoyStatus.UNKNOWN


def _gameplay(start_data: dict[str, Any]) -> dict[str, Any]:
    map_data = start_data.get("map") if isinstance(start_data.get("map"), dict) else {}
    gameplay = map_data.get("gameplay") if isinstance(map_data.get("gameplay"), dict) else {}
    return gameplay


def _top_or_gameplay(start_data: dict[str, Any], key: str) -> Any:
    value = start_data.get(key)
    if value not in (None, [], {}):
        return value
    return _gameplay(start_data).get(key)


def parse_player_state(player_id: str, data: dict[str, Any]) -> PlayerState:
    resources = data.get("resources") if isinstance(data.get("resources"), dict) else {}
    buffs_raw = data.get("buffs") if isinstance(data.get("buffs"), list) else []
    buffs = tuple(str(item.get("type")) for item in buffs_raw if isinstance(item, dict) and item.get("type"))
    return PlayerState(
        player_id=str(_first_present(data, "playerId", default=player_id)),
        team_id=_first_present(data, "teamId"),
        status=_as_status(_first_present(data, "state", "status", default="UNKNOWN")),
        station=_first_present(data, "currentNodeId", "station", "stationId", "node", "position"),
        target=_first_present(data, "nextNodeId", "targetNodeId", "target"),
        route_edge_id=_first_present(data, "routeEdgeId"),
        route_type=_first_present(data, "routeType"),
        good_fruit=_as_int(_first_present(data, "goodFruit", default=100), 100),
        frozen_good_fruit=_as_int(_first_present(data, "frozenGoodFruit", default=0), 0),
        bad_fruit=_as_int(_first_present(data, "badFruit", default=0), 0),
        freshness=_as_float(_first_present(data, "freshness", default=100.0), 100.0),
        task_score_base=_as_int(_first_present(data, "taskScore", "taskScoreBase", default=0), 0),
        bounty_score=_as_int(_first_present(data, "bountyScore", default=0), 0),
        total_score=_as_int(_first_present(data, "totalScore", default=0), 0),
        delivered=bool(_first_present(data, "delivered", default=False)),
        verified=bool(_first_present(data, "verified", default=False)),
        retired=bool(_first_present(data, "retired", default=False)),
        resources={str(k): _as_int(v) for k, v in resources.items()},
        squad_available=_as_int(_first_present(data, "squadAvailable", "squadMembers", default=8), 8),
        squad_in_flight=_as_int(_first_present(data, "squadInFlight", default=0), 0),
        guard_points=_as_int(_first_present(data, "guardActionPoint", "guardPoints", default=4), 4),
        rush_tactic_used_count=_as_int(_first_present(data, "rushTacticUsedCount", default=0), 0),
        buffs=buffs,
        current_process=data.get("currentProcess") if isinstance(data.get("currentProcess"), dict) else None,
        raw=data,
    )


def parse_game_state(player_id: str, start_data: dict[str, Any], inquire_data: dict[str, Any]) -> GameState:
    frame = _as_int(_first_present(inquire_data, "round", "frame", "turn", default=start_data.get("round", 0)), 0)
    max_frame = _as_int(_first_present(inquire_data, "durationRound", default=start_data.get("durationRound", 600)), 600)
    phase = str(_first_present(inquire_data, "phase", default="UNKNOWN"))

    players_raw = inquire_data.get("players") if isinstance(inquire_data.get("players"), list) else start_data.get("players", [])
    me_raw: dict[str, Any] = {}
    opponent_raw: dict[str, Any] | None = None
    if isinstance(players_raw, list):
        for item in players_raw:
            if not isinstance(item, dict):
                continue
            if str(item.get("playerId")) == str(player_id):
                me_raw = item
            elif opponent_raw is None:
                opponent_raw = item

    roles = _gameplay(start_data).get("roles", {})
    if not isinstance(roles, dict):
        roles = {}

    stations = _parse_stations(start_data, inquire_data)
    edges = _parse_edges(start_data, inquire_data)
    resources = _parse_resources(start_data, inquire_data, stations)
    tasks = _parse_tasks(inquire_data)
    windows = _parse_windows(player_id, inquire_data)
    weather = _parse_weather(inquire_data)

    me = parse_player_state(player_id, me_raw)
    return GameState(
        frame=frame,
        max_frame=max_frame,
        phase=phase,
        player_id=str(player_id),
        roles=roles,
        me=me,
        opponent=parse_player_state("opponent", opponent_raw) if isinstance(opponent_raw, dict) else None,
        stations=stations,
        edges=edges,
        tasks=tasks,
        resources=resources,
        windows=windows,
        weather=weather,
        events=list(inquire_data.get("events", []) if isinstance(inquire_data.get("events"), list) else []),
        action_results=list(inquire_data.get("actionResults", []) if isinstance(inquire_data.get("actionResults"), list) else []),
        raw={"start": start_data, "inquire": inquire_data},
    )


def _parse_stations(start_data: dict[str, Any], inquire_data: dict[str, Any]) -> dict[str, Station]:
    raw_nodes = inquire_data.get("nodes") or start_data.get("nodes") or []
    stations: dict[str, Station] = {}
    if not isinstance(raw_nodes, list):
        return stations
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        node_id = _first_present(item, "nodeId", "id")
        if not node_id:
            continue
        guard = item.get("guard") if isinstance(item.get("guard"), dict) else {}
        stock = item.get("resourceStock") if isinstance(item.get("resourceStock"), dict) else {}
        stations[str(node_id)] = Station(
            id=str(node_id),
            name=str(_first_present(item, "name", default="")),
            node_type=str(_first_present(item, "nodeType", "type", default="")),
            process_type=_first_present(item, "processType"),
            process_round=_as_int(_first_present(item, "processRound", default=0), 0),
            has_obstacle=bool(_first_present(item, "hasObstacle", default=False)),
            guard_owner=_first_present(guard, "ownerTeamId"),
            guard_defense=_as_int(_first_present(guard, "defense", default=0), 0),
            can_window=bool(_first_present(item, "canWindow", default=False)),
            resource_stock={str(k): _as_int(v) for k, v in stock.items()},
            raw=item,
        )
    return stations


def _parse_edges(start_data: dict[str, Any], inquire_data: dict[str, Any]) -> list[RouteEdge]:
    raw_edges = inquire_data.get("edges") or start_data.get("edges") or []
    edges: list[RouteEdge] = []
    if not isinstance(raw_edges, list):
        return edges
    for item in raw_edges:
        if not isinstance(item, dict):
            continue
        start = _first_present(item, "fromNodeId", "start", "from", "source")
        end = _first_present(item, "toNodeId", "end", "to", "target")
        if not start or not end:
            continue
        direction = str(_first_present(item, "direction", default="BIDIRECTIONAL")).upper()
        bidirectional = bool(_first_present(item, "bidirectional", "twoWay", default=direction != "ONE_WAY"))
        edges.append(RouteEdge(
            id=str(_first_present(item, "edgeId", "id", default=f"{start}->{end}")),
            start=str(start),
            end=str(end),
            route_type=str(_first_present(item, "routeType", "type", default="ROAD")),
            distance=max(1, _as_int(_first_present(item, "distance", "length", default=1), 1)),
            bidirectional=bidirectional,
        ))
    return edges


def _parse_resources(start_data: dict[str, Any], inquire_data: dict[str, Any], stations: dict[str, Station]) -> list[ResourceStock]:
    resources: list[ResourceStock] = []
    claim_rounds: dict[tuple[str, str], int] = {}
    static_resources = _top_or_gameplay(start_data, "resources") or []
    if isinstance(static_resources, list):
        for item in static_resources:
            if isinstance(item, dict):
                node_id = item.get("nodeId")
                resource_type = item.get("resourceType")
                if node_id and resource_type:
                    claim_rounds[(str(node_id), str(resource_type))] = _as_int(item.get("claimRound"), 2)

    for station in stations.values():
        for resource_type, amount in station.resource_stock.items():
            if amount > 0:
                resources.append(ResourceStock(
                    station=station.id,
                    resource_type=resource_type,
                    amount=amount,
                    claim_frames=claim_rounds.get((station.id, resource_type), 2),
                ))
    return resources


def _parse_tasks(inquire_data: dict[str, Any]) -> list[TaskInstance]:
    raw_tasks = inquire_data.get("tasks") if isinstance(inquire_data.get("tasks"), list) else []
    tasks: list[TaskInstance] = []
    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        task_id = item.get("taskId")
        node_id = item.get("nodeId")
        if not task_id or not node_id:
            continue
        tasks.append(TaskInstance(
            id=str(task_id),
            template=str(_first_present(item, "taskTemplateId", "templateId", default="")),
            target=str(node_id),
            route_bucket=str(_first_present(item, "routeBucket", default="")),
            score=_as_int(_first_present(item, "score", default=0), 0),
            process_frames=_as_int(_first_present(item, "processRound", default=0), 0),
            refresh_frame=_as_int(_first_present(item, "refreshRound", default=0), 0),
            expire_frame=_as_int(_first_present(item, "expireRound", default=0), 0),
            active=bool(_first_present(item, "active", default=True)),
            completed=bool(_first_present(item, "completed", default=False)),
            failed=bool(_first_present(item, "failed", default=False)),
            owner_player_id=_first_present(item, "ownerPlayerId"),
            protection_player_id=_first_present(item, "protectionPlayerId"),
            raw=item,
        ))
    return tasks


def _parse_windows(player_id: str, inquire_data: dict[str, Any]) -> list[WindowState]:
    raw_windows = inquire_data.get("contests") if isinstance(inquire_data.get("contests"), list) else []
    windows: list[WindowState] = []
    for item in raw_windows:
        if not isinstance(item, dict):
            continue
        contest_id = item.get("contestId")
        if not contest_id:
            continue
        status = str(_first_present(item, "status", default=""))
        active = not bool(item.get("resolved")) and status != "SUPPRESSED"
        participant = str(item.get("redPlayerId")) == str(player_id) or str(item.get("bluePlayerId")) == str(player_id)
        windows.append(WindowState(
            id=str(contest_id),
            window_type=str(_first_present(item, "contestType", default="UNKNOWN")),
            target=_first_present(item, "targetNodeId"),
            resource_type=_first_present(item, "resourceType"),
            task_id=_first_present(item, "taskId"),
            active=active,
            my_turn=active and participant,
            round_index=_as_int(_first_present(item, "roundIndex", default=0), 0),
            red_point=_as_int(_first_present(item, "redPoint", default=0), 0),
            blue_point=_as_int(_first_present(item, "bluePoint", default=0), 0),
            status=status,
            raw=item,
        ))
    return windows


def _parse_weather(inquire_data: dict[str, Any]) -> WeatherState | None:
    raw = inquire_data.get("weather")
    if not isinstance(raw, dict):
        return None
    active = raw.get("active") if isinstance(raw.get("active"), list) else []
    forecast = raw.get("forecast") if isinstance(raw.get("forecast"), list) else []
    return WeatherState(
        active_types=tuple(str(item.get("type")) for item in active if isinstance(item, dict) and item.get("type")),
        forecast_types=tuple(str(item.get("type")) for item in forecast if isinstance(item, dict) and item.get("type")),
        raw=raw,
    )
