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
    status: ConvoyStatus = ConvoyStatus.UNKNOWN
    station: str | None = None
    target: str | None = None
    good_fruit: int = 100
    bad_fruit: int = 0
    freshness: float = 100.0
    task_score_base: int = 0
    delivered: bool = False
    verified: bool = False
    resources: dict[str, int] = field(default_factory=dict)
    squad_members: int = 8
    guard_points: int = 4
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


def parse_player_state(player_id: str, data: dict[str, Any]) -> PlayerState:
    resources = _first_present(data, ("resources", "inventory", "items"), {}) or {}
    return PlayerState(
        player_id=player_id,
        status=_as_status(_first_present(data, ("status", "convoyStatus", "state"), "UNKNOWN")),
        station=_first_present(data, ("station", "stationId", "node", "position", "currentNode")),
        target=_first_present(data, ("target", "targetStation", "targetNode")),
        good_fruit=int(_first_present(data, ("goodFruit", "good_fruit", "good"), 100) or 0),
        bad_fruit=int(_first_present(data, ("badFruit", "bad_fruit", "bad"), 0) or 0),
        freshness=float(_first_present(data, ("freshness", "fresh", "quality"), 100.0) or 0),
        task_score_base=int(_first_present(data, ("taskScoreBase", "task_score_base", "taskScore"), 0) or 0),
        delivered=bool(_first_present(data, ("delivered", "isDelivered"), False)),
        verified=bool(_first_present(data, ("verified", "gateVerified", "hasVerified"), False)),
        resources={str(k): int(v) for k, v in resources.items()} if isinstance(resources, dict) else {},
        squad_members=int(_first_present(data, ("squadMembers", "squad", "squad_members"), 8) or 0),
        guard_points=int(_first_present(data, ("guardPoints", "guard_points", "guardActionPoints"), 4) or 0),
        raw=data,
    )


def parse_game_state(player_id: str, payload: dict[str, Any]) -> GameState:
    """Best-effort parser for unknown official JSON fields.

    Keep this parser permissive. Once the official protocol is available,
    narrow field names here instead of touching strategy code.
    """
    state = payload.get("state", payload)
    frame = int(_first_present(state, ("frame", "turn", "round", "tick"), 0) or 0)
    max_frame = int(_first_present(state, ("maxFrame", "max_frame", "totalFrames"), 600) or 600)
    phase = str(_first_present(state, ("phase", "stage"), "UNKNOWN"))

    players = _first_present(state, ("players", "playerStates", "teams"), {}) or {}
    me_raw: dict[str, Any]
    opponent_raw: dict[str, Any] | None = None
    if isinstance(players, dict):
        me_raw = players.get(player_id) or players.get(str(player_id)) or state.get("me") or state.get("self") or {}
        for pid, pdata in players.items():
            if str(pid) != str(player_id):
                opponent_raw = pdata if isinstance(pdata, dict) else None
                break
    else:
        me_raw = state.get("me") or state.get("self") or {}

    if not isinstance(me_raw, dict):
        me_raw = {}

    stations: dict[str, Station] = {}
    raw_stations = _first_present(state, ("stations", "nodes", "mapNodes"), {}) or {}
    if isinstance(raw_stations, dict):
        for sid, s in raw_stations.items():
            if isinstance(s, dict):
                stations[str(sid)] = Station(
                    id=str(_first_present(s, ("id", "stationId"), sid)),
                    name=str(_first_present(s, ("name",), "")),
                    kind=str(_first_present(s, ("type", "kind"), "")),
                    neighbors=tuple(str(x) for x in _first_present(s, ("neighbors", "adjacent"), ()) or ()),
                )

    edges: list[RouteEdge] = []
    raw_edges = _first_present(state, ("edges", "routes", "routeEdges"), []) or []
    if isinstance(raw_edges, list):
        for edge in raw_edges:
            if not isinstance(edge, dict):
                continue
            start = _first_present(edge, ("start", "from", "source"))
            end = _first_present(edge, ("end", "to", "target"))
            if start and end:
                edges.append(RouteEdge(
                    start=str(start),
                    end=str(end),
                    route_type=str(_first_present(edge, ("routeType", "type"), "ROAD")),
                    distance=int(_first_present(edge, ("distance", "length"), 1) or 1),
                    bidirectional=bool(_first_present(edge, ("bidirectional", "twoWay"), True)),
                ))

    tasks: list[TaskInstance] = []
    for t in _first_present(state, ("tasks", "taskList", "activeTasks"), []) or []:
        if not isinstance(t, dict):
            continue
        tid = _first_present(t, ("id", "taskId", "instanceId"))
        target = _first_present(t, ("target", "targetNode", "station", "stationId"))
        if tid and target:
            tasks.append(TaskInstance(
                id=str(tid),
                template=str(_first_present(t, ("template", "templateId", "type"), "")),
                target=str(target),
                score=int(_first_present(t, ("score", "points"), 30) or 0),
                process_frames=int(_first_present(t, ("processFrames", "duration"), 0) or 0),
                expire_frame=_first_present(t, ("expireFrame", "expire", "deadline")),
                status=str(_first_present(t, ("status",), "ACTIVE")),
            ))

    resources: list[ResourceStock] = []
    for r in _first_present(state, ("resources", "resourceStocks", "stocks"), []) or []:
        if not isinstance(r, dict):
            continue
        station = _first_present(r, ("station", "stationId", "node"))
        rtype = _first_present(r, ("resourceType", "type", "name"))
        if station and rtype:
            resources.append(ResourceStock(
                station=str(station),
                resource_type=str(rtype),
                amount=int(_first_present(r, ("amount", "count"), 0) or 0),
                claim_frames=int(_first_present(r, ("claimFrames", "duration"), 2) or 2),
            ))

    windows: list[WindowState] = []
    for w in _first_present(state, ("windows", "windowList", "contests"), []) or []:
        if not isinstance(w, dict):
            continue
        wid = _first_present(w, ("id", "windowId"))
        if wid:
            windows.append(WindowState(
                id=str(wid),
                window_type=str(_first_present(w, ("type", "windowType"), "UNKNOWN")),
                target=_first_present(w, ("target", "targetNode", "station")),
                active=bool(_first_present(w, ("active", "isActive"), True)),
                my_turn=bool(_first_present(w, ("myTurn", "canPlay", "needAction"), True)),
                raw=w,
            ))

    weather = None
    weather_raw = _first_present(state, ("weather", "currentWeather"), None)
    if isinstance(weather_raw, dict):
        weather = WeatherState(
            weather_type=_first_present(weather_raw, ("type", "weatherType")),
            area=tuple(str(x) for x in _first_present(weather_raw, ("area", "nodes"), ()) or ()),
            start_frame=_first_present(weather_raw, ("startFrame", "start")),
            duration=_first_present(weather_raw, ("duration",)),
            raw=weather_raw,
        )

    return GameState(
        frame=frame,
        max_frame=max_frame,
        phase=phase,
        me=parse_player_state(player_id, me_raw),
        opponent=parse_player_state("opponent", opponent_raw) if isinstance(opponent_raw, dict) else None,
        stations=stations,
        edges=edges,
        tasks=tasks,
        resources=resources,
        windows=windows,
        weather=weather,
        raw=payload,
    )
