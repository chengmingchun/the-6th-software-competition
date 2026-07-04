from __future__ import annotations

import unittest

from lizhi_agent.models import GameState, PlayerState, RouteEdge, WeatherState
from lizhi_agent.route_planner import RoutePlanner


class RoutePlannerTest(unittest.TestCase):
    def test_lowercase_water_route_keeps_water_cost_advantage_when_fresh(self) -> None:
        state = GameState(
            player_id="1001",
            me=PlayerState(player_id="1001", station="S01", freshness=100, task_score_base=30, resources={"ICE_BOX": 1}),
            edges=[
                RouteEdge(id="road", start="S01", end="S14", route_type="ROAD", distance=12),
                RouteEdge(id="water-a", start="S01", end="S02", route_type="water", distance=4),
                RouteEdge(id="water-b", start="S02", end="S14", route_type="water", distance=4),
            ],
        )
        plan = RoutePlanner().plan(state, "S01", "S14")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.path, ("S01", "S02", "S14"))

    def test_high_score_low_freshness_prefers_road_over_water_shortcut(self) -> None:
        state = GameState(
            player_id="1001",
            me=PlayerState(player_id="1001", station="S01", freshness=82, task_score_base=135, resources={}),
            edges=[
                RouteEdge(id="road", start="S01", end="S14", route_type="ROAD", distance=10),
                RouteEdge(id="water-a", start="S01", end="S02", route_type="water", distance=4),
                RouteEdge(id="water-b", start="S02", end="S14", route_type="water", distance=4),
            ],
        )
        plan = RoutePlanner().plan(state, "S01", "S14")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.path, ("S01", "S14"))

    def test_heavy_rain_weather_penalizes_water_route_time(self) -> None:
        state = GameState(
            player_id="1001",
            me=PlayerState(player_id="1001", station="S01", freshness=100, task_score_base=30, resources={"ICE_BOX": 1}),
            weather=WeatherState(active_types=("HEAVY_RAIN",)),
            edges=[
                RouteEdge(id="road", start="S01", end="S14", route_type="ROAD", distance=9),
                RouteEdge(id="water-a", start="S01", end="S02", route_type="WATER", distance=4),
                RouteEdge(id="water-b", start="S02", end="S14", route_type="WATER", distance=4),
            ],
        )
        plan = RoutePlanner().plan(state, "S01", "S14")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.path, ("S01", "S14"))


if __name__ == "__main__":
    unittest.main()
