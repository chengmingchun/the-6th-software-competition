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
    kind: str = ""
    process_type: str | None = None
    process_round: int = 0
    neighbors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteEdge:
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
    score: int = 0
    process_frames: int = 0
    expire_frame: int | None = None
    status: str = "ACTIVE"
    owner_player_id: int | None = None
    protection_player_id: int | None = None

    @property
    def is_valuable(self) -> bool:
        return self.score >= 30 or self.template in {"T01", "T02", "T04", "T06", "T08", "T11"}


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
    active: bool = False
    my_turn: bool = False
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeatherState:
    weather_type: str | None = None
    area: tuple[str, ...] = ()
    start_frame: int | None = None
    duration: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlayerState:
    player_id: str
    team_id: str | None = None
    status: ConvoyStatus = ConvoyStatus.UNKNOWN
    station: str | None = None
    target: str | None = None
    route_edge_id: str | None = None
    good_fruit: int = 100
    bad_fruit: int = 0
    freshness: float = 100.0
    task_score_base: int = 0
    bounty_score: int = 0
    total_score: int = 0
    delivered: bool = False
    verified: bool = False
    retired: bool = False
    resources: dict[str, int] = field(default_factory=dict)
    squad_members: int = 8
    squad_in_flight: int = 0
    guard_points: int = 4
    rush_tactic_used_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def has_resource(self, resource_type: str) -> bool:
        return self.resources.get(resource_type, 0) > 0

    @property
    def can_act_on_station(self) -> bool:
        return self.status in {ConvoyStatus.IDLE, ConvoyStatus.WAITING, ConvoyStatus.UNKNOWN}


@dataclass(frozen=True)
class GameState:
    frame: int = 0
    max_frame: int = 600
    match_id: str | None = None
    phase: str = "UNKNOWN"
    me: PlayerState = field(default_factory=lambda: PlayerState(player_id="player0"))
    opponent: PlayerState | None = None
    stations: dict[str, Station] = field(default_factory=dict)
    edges: list[RouteEdge] = field(default_factory=list)
    tasks: list[TaskInstance] = field(default_factory=list)
    resources: list[ResourceStock] = field(default_factory=list)
    windows: list[WindowState] = field(default_factory=list)
    weather: WeatherState | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def turns_left(self) -> int:
        return max(0, self.max_frame - self.frame)

    def active_window(self) -> WindowState | None:
        for window in self.windows:
            if window.active and window.my_turn:
                return window
        return None

    def station_resources(self, station_id: str | None) -> list[ResourceStock]:
        if station_id is None:
            return []
        return [r for r in self.resources if r.station == station_id and r.amount > 0]

    def station_tasks(self, station_id: str | None) -> list[TaskInstance]:
        if station_id is None:
            return []
        return [t for t in self.tasks if t.target == station_id and t.status == "ACTIVE"]

    def neighbors(self, station_id: str | None) -> list[str]:
        if station_id is None:
            return []
        result: list[str] = []
        for edge in self.edges:
            other = edge.other(station_id)
            if other is not None:
                result.append(other)
        if result:
            return result
        station = self.stations.get(station_id)
        return list(station.neighbors) if station else []


def _first_present(data: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_status(value: Any) -> ConvoyStatus:
    if isinstance(value, str):
        try:
            return ConvoyStatus(value)
        except ValueError:
            return ConvoyStatus.UNKNOWN
    return ConvoyStatus.UNKNOWN


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_player_state(player_id: str, data: dict[str, Any]) -> PlayerState:
    resources = _first_present(data, ("resources", "inventory", "items"), {}) or {}
    return PlayerState(
        player_id=str(_first_present(data, ("playerId", "id"), player_id)),
        team_id=_first_present(data, ("teamId", "team")),
        status=_as_status(_first_present(data, ("state", "status", "convoyStatus"), "UNKNOWN")),
        station=_first_present(data, ("currentNodeId", "station", "stationId", "node", "position", "currentNode")),
        target=_first_present(data, ("nextNodeId", "target", "targetStation", "targetNode")),
        route_edge_id=_first_present(data, ("routeEdgeId",)),
        good_fruit=_as_int(_first_present(data, ("goodFruit", "good_fruit", "good"), 100), 100),
        bad_fruit=_as_int(_first_present(data, ("badFruit", "bad_fruit", "bad"), 0), 0),
        freshness=float(_first_present(data, ("freshness", "fresh", "quality"), 100.0) or 0),
        task_score_base=_as_int(_first_present(data, ("taskScore", "taskScoreBase", "task_score_base"), 0), 0),
        bounty_score=_as_int(_first_present(data, ("bountyScore",), 0), 0),
        total_score=_as_int(_first_present(data, ("totalScore", "totalGold"), 0), 0),
        delivered=bool(_first_present(data, ("delivered", "isDelivered"), False)),
        verified=bool(_first_present(data, ("verified", "gateVerified", "hasVerified"), False)),
        retired=bool(_first_present(data, ("retired",), False)),
        resources={str(k): _as_int(v, 0) for k, v in resources.items()} if isinstance(resources, dict) else {},
        squad_members=_as_int(_first_present(data, ("squadAvailable", "squadMembers", "squad", "squad_members"), 8), 8),
        squad_in_flight=_as_int(_first_present(data, ("squadInFlight",), 0), 0),
        guard_points=_as_int(_first_present(data, ("guardActionPoint", "guardPoints", "guard_points", "guardActionPoints"), 4), 4),
        rush_tactic_used_count=_as_int(_first_present(data, ("rushTacticUsedCount",), 0), 0),
        raw=data,
    )


def parse_stations(nodes: list[Any]) -> dict[str, Station]:
    stations: dict[str, Station] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        sid = _first_present(node, ("nodeId", "id", "stationId"))
        if not sid:
            continue
        stations[str(sid)] = Station(
            id=str(sid),
            name=str(_first_present(node, ("name",), "")),
            kind=str(_first_present(node, ("nodeType", "type", "kind"), "")),
            process_type=_first_present(node, ("processType",)),
            process_round=_as_int(_first_present(node, ("processRound",), 0), 0),
            neighbors=tuple(str(x) for x in _first_present(node, ("neighbors", "adjacent"), ()) or ()),
        )
    return stations


def parse_edges(edges_raw: list[Any]) -> list[RouteEdge]:
    edges: list[RouteEdge] = []
    for edge in edges_raw:
        if not isinstance(edge, dict):
            continue
        start = _first_present(edge, ("fromNodeId", "fromNode", "start", "from", "source"))
        end = _first_present(edge, ("toNodeId", "toNode", "end", "to", "target"))
        if start and end:
            edges.append(RouteEdge(
                start=str(start),
                end=str(end),
                route_type=str(_first_present(edge, ("routeType", "type"), "ROAD")),
                distance=_as_int(_first_present(edge, ("distance", "length"), 1), 1),
                bidirectional=bool(_first_present(edge, ("bidirectional", "twoWay"), True)),
            ))
    return edges


def parse_tasks(tasks_raw: list[Any]) -> list[TaskInstance]:
    tasks: list[TaskInstance] = []
    for t in tasks_raw:
        if not isinstance(t, dict):
            continue
        tid = _first_present(t, ("taskId", "id", "instanceId"))
        target = _first_present(t, ("nodeId", "targetNodeId", "target", "targetNode", "station", "stationId"))
        if not (tid and target):
            continue
        active = bool(_first_present(t, ("active",), True))
        completed = bool(_first_present(t, ("completed",), False))
        failed = bool(_first_present(t, ("failed",), False))
        status = "ACTIVE" if active and not completed and not failed else "INACTIVE"
        tasks.append(TaskInstance(
            id=str(tid),
            template=str(_first_present(t, ("taskTemplateId", "template", "templateId", "type"), "")),
            target=str(target),
            score=_as_int(_first_present(t, ("score", "points"), 30), 30),
            process_frames=_as_int(_first_present(t, ("processRound", "processFrames", "duration"), 0), 0),
            expire_frame=_first_present(t, ("expireRound", "expireFrame", "expire", "deadline")),
            status=status,
            owner_player_id=_first_present(t, ("ownerPlayerId",)),
            protection_player_id=_first_present(t, ("protectionPlayerId",)),
        ))
    return tasks


def parse_resources_from_start(resources_raw: list[Any]) -> list[ResourceStock]:
    resources: list[ResourceStock] = []
    for r in resources_raw:
        if not isinstance(r, dict):
            continue
        station = _first_present(r, ("nodeId", "station", "stationId", "node"))
        rtype = _first_present(r, ("resourceType", "type", "name"))
        if station and rtype:
            resources.append(ResourceStock(
                station=str(station),
                resource_type=str(rtype),
                amount=_as_int(_first_present(r, ("count", "amount"), 0), 0),
                claim_frames=_as_int(_first_present(r, ("claimRound", "claimFrames", "duration"), 2), 2),
            ))
    return resources


def parse_resources_from_nodes(nodes_raw: list[Any], fallback: list[ResourceStock]) -> list[ResourceStock]:
    resources: list[ResourceStock] = []
    claim_round_by_key = {(r.station, r.resource_type): r.claim_frames for r in fallback}
    for node in nodes_raw:
        if not isinstance(node, dict):
            continue
        station = _first_present(node, ("nodeId", "id", "stationId"))
        stock = node.get("resourceStock")
        if not station or not isinstance(stock, dict):
            continue
        for rtype, amount in stock.items():
            count = _as_int(amount, 0)
            if count > 0:
                resources.append(ResourceStock(
                    station=str(station),
                    resource_type=str(rtype),
                    amount=count,
                    claim_frames=claim_round_by_key.get((str(station), str(rtype)), 2),
                ))
    return resources or fallback


def parse_windows(contests_raw: list[Any], player_id: str) -> list[WindowState]:
    windows: list[WindowState] = []
    for w in contests_raw:
        if not isinstance(w, dict):
            continue
        status = _first_present(w, ("status",), None)
        if status == "SUPPRESSED":
            continue
        if bool(_first_present(w, ("resolved",), False)):
            continue
        wid = _first_present(w, ("contestId", "id", "windowId"))
        if not wid:
            continue
        red = _first_present(w, ("redPlayerId",), None)
        blue = _first_present(w, ("bluePlayerId",), None)
        involved = str(red) == str(player_id) or str(blue) == str(player_id)
        windows.append(WindowState(
            id=str(wid),
            window_type=str(_first_present(w, ("contestType", "type", "windowType"), "UNKNOWN")),
            target=_first_present(w, ("targetNodeId", "target", "targetNode", "station")),
            active=involved,
            my_turn=involved,
            status=status,
            raw=w,
        ))
    return windows


def parse_weather(weather_raw: Any) -> WeatherState | None:
    if not isinstance(weather_raw, dict):
        return None
    return WeatherState(
        weather_type=_first_present(weather_raw, ("type", "weatherType", "currentType")),
        area=tuple(str(x) for x in _first_present(weather_raw, ("area", "nodes", "regionNodeIds"), ()) or ()),
        start_frame=_first_present(weather_raw, ("startFrame", "startRound", "start")),
        duration=_first_present(weather_raw, ("duration", "durationRound")),
        raw=weather_raw,
    )


def parse_game_state(player_id: str, payload: dict[str, Any], start_data: dict[str, Any] | None = None) -> GameState:
    """Parse official start/inquire payloads into strategy-friendly state."""
    state = payload.get("msg_data", payload.get("state", payload))
    start = start_data or {}
    start_map = start.get("map", {}) if isinstance(start.get("map"), dict) else {}
    gameplay = start_map.get("gameplay", {}) if isinstance(start_map.get("gameplay"), dict) else {}

    frame = _as_int(_first_present(state, ("round", "frame", "turn", "tick"), _first_present(start, ("round",), 0)), 0)
    max_frame = _as_int(_first_present(state, ("durationRound", "maxFrame", "max_frame", "totalFrames"), _first_present(start, ("durationRound",), 600)), 600)
    phase = str(_first_present(state, ("phase", "stage"), "UNKNOWN"))
    match_id = _first_present(state, ("matchId",), _first_present(start, ("matchId",), None))

    players_raw = _first_present(state, ("players", "playerStates", "teams"), []) or []
    me_raw: dict[str, Any] = {}
    opponent_raw: dict[str, Any] | None = None
    if isinstance(players_raw, list):
        for p in players_raw:
            if not isinstance(p, dict):
                continue
            if str(p.get("playerId")) == str(player_id):
                me_raw = p
            elif opponent_raw is None:
                opponent_raw = p
    elif isinstance(players_raw, dict):
        me_raw = players_raw.get(player_id) or players_raw.get(str(player_id)) or {}
        for pid, pdata in players_raw.items():
            if str(pid) != str(player_id) and isinstance(pdata, dict):
                opponent_raw = pdata
                break
    if not me_raw:
        me_raw = state.get("me") or state.get("self") or {}

    nodes_raw = _first_present(state, ("nodes",), None)
    if nodes_raw is None:
        nodes_raw = _first_present(start, ("nodes",), start_map.get("nodes", []))
    edges_raw = _first_present(state, ("edges",), None)
    if edges_raw is None:
        edges_raw = _first_present(start, ("edges",), start_map.get("edges", []))

    stations = parse_stations(nodes_raw if isinstance(nodes_raw, list) else [])
    edges = parse_edges(edges_raw if isinstance(edges_raw, list) else [])

    start_resources_raw = _first_present(start, ("resources",), gameplay.get("resources", []))
    fallback_resources = parse_resources_from_start(start_resources_raw if isinstance(start_resources_raw, list) else [])
    resources = parse_resources_from_nodes(nodes_raw if isinstance(nodes_raw, list) else [], fallback_resources)

    tasks = parse_tasks(_first_present(state, ("tasks", "taskList", "activeTasks"), []) or [])
    windows = parse_windows(_first_present(state, ("contests", "windows", "windowList"), []) or [], str(player_id))
    weather = parse_weather(_first_present(state, ("weather", "currentWeather"), None))

    return GameState(
        frame=frame,
        max_frame=max_frame,
        match_id=match_id,
        phase=phase,
        me=parse_player_state(str(player_id), me_raw if isinstance(me_raw, dict) else {}),
        opponent=parse_player_state("opponent", opponent_raw) if isinstance(opponent_raw, dict) else None,
        stations=stations,
        edges=edges,
        tasks=tasks,
        resources=resources,
        windows=windows,
        weather=weather,
        raw={"current": payload, "start": start},
    )
