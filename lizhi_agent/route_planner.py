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


@dataclass(frozen=True)
class RoutePlan:
    path: tuple[str, ...]
    estimated_frames: int

    @property
    def next_station(self) -> str | None:
        return self.path[1] if len(self.path) >= 2 else None


class RoutePlanner:
    """Weighted planner over the official node/edge graph.

    This mirrors the resource-manager style used by RTS bots: the planner owns
    graph costs, while strategy only asks for candidate next hops.  Costs are
    intentionally conservative because weather and forced-pass taxes can change
    frame by frame.
    """

    def plan(self, state: GameState, start: str | None, target: str) -> RoutePlan | None:
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
                next_cost = cost + self._edge_frames(edge) + self._node_penalty(state, nxt)
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

    def _edge_frames(self, edge: RouteEdge) -> int:
        # Official packets and local fixtures are not perfectly consistent about
        # route type casing.  Treat water/mountain/branch case-insensitively;
        # otherwise lowercase "water" silently fell back to ROAD and made the
        # planner over-prefer long road-only routes.
        route_type = str(edge.route_type or "ROAD").upper()
        coefficient = ROUTE_COEFFICIENT.get(route_type, ROUTE_COEFFICIENT["ROAD"])
        required_move = edge.distance * coefficient
        return max(1, (required_move + 999) // 1000)

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
