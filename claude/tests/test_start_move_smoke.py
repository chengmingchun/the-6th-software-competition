from __future__ import annotations

import unittest

from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, PlayerState, RouteEdge
from lizhi_agent.strategy import BaselineStrategy


class ClaudeStartMoveSmokeTest(unittest.TestCase):
    def test_idle_at_start_moves_to_s02(self):
        strategy = BaselineStrategy("1001", StrategyConfig.default(), DecisionLogger("test"))
        state = GameState(
            frame=1,
            phase="NORMAL",
            player_id="1001",
            roles={"startNodeId": "S01", "gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01"),
            edges=[
                RouteEdge(id="E01", start="S01", end="S02", route_type="ROAD", distance=5),
                RouteEdge(id="E02", start="S02", end="S03", route_type="ROAD", distance=5),
                RouteEdge(id="E03", start="S03", end="S14", route_type="ROAD", distance=5),
            ],
        )
        actions = strategy.decide(state).to_actions()
        self.assertEqual(actions[0]["action"], "MOVE")
        self.assertEqual(actions[0]["targetNodeId"], "S02")

    def test_idle_at_start_falls_back_to_main_route_without_edges(self):
        strategy = BaselineStrategy("1001", StrategyConfig.default(), DecisionLogger("test"))
        state = GameState(
            frame=1,
            phase="NORMAL",
            player_id="1001",
            roles={"startNodeId": "S01", "gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01"),
            edges=[],
        )
        actions = strategy.decide(state).to_actions()
        self.assertEqual(actions[0]["action"], "MOVE")
        self.assertEqual(actions[0]["targetNodeId"], "S02")


if __name__ == "__main__":
    unittest.main()
