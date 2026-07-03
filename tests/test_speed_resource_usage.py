from __future__ import annotations

import unittest

from lizhi_agent.actions import MainActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import GameState, PlayerState, RouteEdge
from lizhi_agent.strategy import BaselineStrategy


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None

    def close(self) -> None:
        return None


class SpeedResourceUsageTest(unittest.TestCase):
    def test_fast_horse_used_for_long_delivery_leg_after_score_floor(self) -> None:
        state = GameState(
            frame=150,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(
                player_id="1001",
                status="IDLE",
                station="S01",
                task_score_base=90,
                resources={"FAST_HORSE": 1},
            ),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=4),
                RouteEdge(id="E2", start="S02", end="S14", distance=4),
                RouteEdge(id="E3", start="S14", end="S15", distance=2),
            ],
        )
        action = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger()).decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "FAST_HORSE")


if __name__ == "__main__":
    unittest.main()
