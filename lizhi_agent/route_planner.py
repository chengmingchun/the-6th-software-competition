from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from lizhi_agent.models import GameState, RouteEdge


@dataclass(frozen=True)
class RoutePlan:
    path: tuple[str, ...]
    estimated_frames: int

    @property
    def next_station(self) -> str | None:
        if len(self.path) >= 2:
            return self.path[1]
        return None


class RoutePlanner:
    """Graph planner over station IDs.

    The first baseline uses BFS because official map weights may not be aligned
    with protocol fields yet. The interface already exposes estimated_frames so
    a weighted Dijkstra implementation can replace it later.
    """

    def plan(self, state: GameState, start: str | None, target: str) -> RoutePlan | None:
        if start is None:
            return None
        if start == target:
            return RoutePlan(path=(start,), estimated_frames=0)

        prev: dict[str, str | None] = {start: None}
        q: deque[str] = deque([start])

        while q:
            cur = q.popleft()
            for nxt in state.neighbors(cur):
                if nxt in prev:
                    continue
                prev[nxt] = cur
                if nxt == target:
                    return self._build_plan(prev, start, target, state)
                q.append(nxt)
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
        if plan is None:
            return 10**9
        return plan.estimated_frames

    def _build_plan(self, prev: dict[str, str | None], start: str, target: str, state: GameState) -> RoutePlan:
        path: list[str] = []
        cur: str | None = target
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return RoutePlan(path=tuple(path), estimated_frames=self._estimate_path_frames(path, state))

    def _estimate_path_frames(self, path: list[str], state: GameState) -> int:
        frames = 0
        for a, b in zip(path, path[1:]):
            edge = self._find_edge(state.edges, a, b)
            if edge is None:
                frames += 8
            else:
                frames += max(1, self._edge_frames(edge))
        return frames

    def _find_edge(self, edges: list[RouteEdge], a: str, b: str) -> RouteEdge | None:
        for edge in edges:
            if edge.start == a and edge.end == b:
                return edge
            if edge.bidirectional and edge.start == b and edge.end == a:
                return edge
        return None

    def _edge_frames(self, edge: RouteEdge) -> int:
        # Rough official constants from the task book; this is intentionally
        # conservative and should be replaced after exact route distances arrive.
        coefficient = {
            "ROAD": 1380,
            "WATER": 1250,
            "MOUNTAIN": 1780,
            "BRANCH": 1550,
        }.get(edge.route_type, 1380)
        required_move = edge.distance * coefficient
        return (required_move + 999) // 1000
