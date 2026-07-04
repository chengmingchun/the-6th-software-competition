from __future__ import annotations

import heapq
from dataclasses import dataclass

from lizhi_agent.models import GameState, RouteEdge


ROUTE_COEFFICIENT = {
    "ROAD": 1380,
    "WATER": 1250,
    "MOUNTAIN": 1780,
    "BRANCH": 1550,
}
WEATHER_MOVE_MULTIPLIER = {
    "NONE": 1000,
    "HOT": 1000,
    "HEAVY_RAIN": 1350,
    "MOUNTAIN_FOG": 1100,
}


@dataclass(frozen=True)
class RoutePlan:
    path: tuple[str, ...]
    estimated_frames: int

    @property
    def next_station(self) -> str | None:
        return self.path[1] if len(self.path) >= 2 else None


class RoutePlanner:
    """Weighted planner over the official node/edge graph.

    The planner scores routes by time plus a freshness-risk premium.  Pure WATER
    shortcuts can arrive earlier but may burn enough freshness to lose score;
    once a convoy has a real task score to protect, ROAD is allowed to beat a
    faster risky route.

    Supports forbidden_nodes: nodes that the convoy should not enter via plain
    MOVE (e.g. guarded chokepoints).  The planner treats them as impassable.
    """

    def plan(self, state: GameState, start: str | None, target: str,
             forbidden_nodes: frozenset[str] | None = None) -> RoutePlan | None:
        if start is None:
            return None
        if start == target:
            return RoutePlan(path=(start,), estimated_frames=0)

        dist: dict[str, int] = {start: 0}
        prev: dict[str, str | None] = {start: None}
        heap: list[tuple[int, str]] = [(0, start)]

        while heap:
            cost, node = heapq.heappop(heap)
            if cost != dist[node]:
                continue
            if node == target:
                return RoutePlan(path=self._rebuild_path(prev, target), estimated_frames=cost)
            for edge, nxt in self._out_edges(state, node):
                # Do not step into forbidden nodes via plain MOVE
                if forbidden_nodes and nxt in forbidden_nodes:
                    # Allowed only if it's the final target (we have to go there)
                    if nxt != target:
                        continue
                next_cost = cost + self._edge_frames(state, edge) + self._node_penalty(state, nxt)
                if next_cost < dist.get(nxt, 10**12):
                    dist[nxt] = next_cost
                    prev[nxt] = node
                    heapq.heappush(heap, (next_cost, nxt))
        return None

    def next_hop_to_any(self, state: GameState, start: str | None, targets: tuple[str, ...]) -> str | None:
        best: RoutePlan | None = None
        for target in targets:
            plan = self.plan(state, start, target)
            if plan is None:
                continue
            if best is None or plan.estimated_frames < best.estimated_frames:
                best = plan
        return best.next_station if best else None

    def estimate_frames(self, state: GameState, start: str | None, target: str) -> int:
        plan = self.plan(state, start, target)
        return plan.estimated_frames if plan else 10**9

    def _out_edges(self, state: GameState, node: str) -> list[tuple[RouteEdge, str]]:
        result: list[tuple[RouteEdge, str]] = []
        for edge in state.edges:
            other = edge.other(node)
            if other is not None:
                result.append((edge, other))
        return result

    def _edge_frames(self, state: GameState, edge: RouteEdge) -> int:
        # Official packets and local fixtures are not perfectly consistent about
        # route type casing.  Treat water/mountain/branch case-insensitively.
        route_type = str(edge.route_type or "ROAD").upper()
        coefficient = ROUTE_COEFFICIENT.get(route_type, ROUTE_COEFFICIENT["ROAD"])
        required_move = edge.distance * coefficient
        weather_mult = self._weather_move_multiplier(state, route_type)
        per_frame = 1_000_000 // max(weather_mult, 1)
        base_frames = max(1, (required_move + per_frame - 1) // per_frame)
        return base_frames + self._freshness_risk_penalty(state, edge, route_type, base_frames)

    def _weather_move_multiplier(self, state: GameState, route_type: str) -> int:
        if state.weather is None:
            return WEATHER_MOVE_MULTIPLIER["NONE"]
        weather_types = set(state.weather.active_types) | set(state.weather.forecast_types)
        if route_type == "WATER" and "HEAVY_RAIN" in weather_types:
            return WEATHER_MOVE_MULTIPLIER["HEAVY_RAIN"]
        if route_type == "MOUNTAIN" and "MOUNTAIN_FOG" in weather_types:
            return WEATHER_MOVE_MULTIPLIER["MOUNTAIN_FOG"]
        return WEATHER_MOVE_MULTIPLIER["NONE"]

    def _freshness_risk_penalty(self, state: GameState, edge: RouteEdge, route_type: str, base_frames: int) -> int:
        """Convert route-type freshness risk into frame-equivalent cost.

        Water shortcuts are still attractive early, with ICE_BOX in hand, or when
        the deadline is genuinely tight.  They become expensive after the convoy
        has 90+ task score, low freshness, no ICE_BOX safety net, or is already
        protecting delivery quality.  This prevents a fast WATER route from
        winning the path search while silently throwing away 10+ freshness.
        """

        if route_type not in {"WATER", "MOUNTAIN"}:
            return 0
        me = state.me
        pressure = 0
        if me.task_score_base >= 90:
            pressure += 1
        if me.task_score_base >= 120:
            pressure += 1
        if me.freshness <= 92:
            pressure += 1
        if me.freshness <= 82:
            pressure += 1
        if not me.has_resource("ICE_BOX"):
            pressure += 1
        if state.turns_left <= 180:
            # Near timeout, time may matter more than quality.  Do not make
            # risky edges impossible when the alternative is failing delivery.
            pressure = max(0, pressure - 1)
        if pressure <= 0:
            return 0
        if route_type == "WATER":
            return max(1, (base_frames * pressure + 1) // 2)
        # Mountain is already slow and usually freshness-risky; add a smaller
        # extra premium so it is not chosen just because of graph topology.
        return max(1, (base_frames * pressure + 2) // 3)

    def _node_penalty(self, state: GameState, node_id: str) -> int:
        station = state.station(node_id)
        if station is None:
            return 0
        penalty = 0
        if station.has_obstacle:
            penalty += 12
        if station.has_enemy_guard(state.me.team_id):
            penalty += 10 + station.guard_defense * 5
        return penalty

    def _rebuild_path(self, prev: dict[str, str | None], target: str) -> tuple[str, ...]:
        path: list[str] = []
        cur: str | None = target
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return tuple(path)
