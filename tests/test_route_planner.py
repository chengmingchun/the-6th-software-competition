from __future__ import annotations

import unittest

from lizhi_agent.models import GameState, PlayerState, RouteEdge
from lizhi_agent.route_planner import RoutePlanner


class RoutePlannerTest(unittest.TestCase):
    def test_lowercase_water_route_keeps_water_cost_advantage(self) -> None:
        state = GameState(
            player_id="1001",
            me=PlayerState(player_id="1001", station="S01"),
            edges=[
                RouteEdge(id="road", start="S01", end="S14", route_type="ROAD", distance=10),
                RouteEdge(id="water-a", start="S01", end="S02", route_type="water", distance=5),
                RouteEdge(id="water-b", start="S02", end="S14", route_type="water", distance=5),
            ],
        )
        plan = RoutePlanner().plan(state, "S01", "S14")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.path, ("S01", "S02", "S14"))


if __name__ == "__main__":
    unittest.main()
