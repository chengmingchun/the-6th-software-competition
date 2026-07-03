from __future__ import annotations

import unittest

from lizhi_agent.actions import MainActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import GameState, PlayerState
from lizhi_agent.strategy import BaselineStrategy


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None

    def close(self) -> None:
        return None


class FreshnessProtectionTest(unittest.TestCase):
    def test_icebox_protects_score_quality_after_target_score(self) -> None:
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status="IDLE",
                station="S09",
                task_score_base=90,
                freshness=95,
                resources={"ICE_BOX": 1},
            ),
        )
        action = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger()).decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_icebox_protects_premium_score_quality_before_big_drop(self) -> None:
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status="IDLE",
                station="S09",
                task_score_base=135,
                freshness=96,
                resources={"ICE_BOX": 1},
            ),
        )
        action = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger()).decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_rejected_icebox_sets_cooldown(self) -> None:
        strategy = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger())
        rejected = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status="IDLE",
                station="S09",
                task_score_base=135,
                freshness=96,
                resources={"ICE_BOX": 1},
            ),
            action_results=[{"playerId": "1001", "action": "USE_RESOURCE", "accepted": False, "code": "RESOURCE_NOT_APPLICABLE", "resourceType": "ICE_BOX"}],
        )
        action = strategy.decide(rejected)
        self.assertTrue(action.main is None or action.main.action != MainActionType.USE_RESOURCE)

        still_cooling = GameState(
            frame=270,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status="IDLE",
                station="S09",
                task_score_base=135,
                freshness=96,
                resources={"ICE_BOX": 1},
            ),
        )
        action = strategy.decide(still_cooling)
        self.assertTrue(action.main is None or action.main.action != MainActionType.USE_RESOURCE)


if __name__ == "__main__":
    unittest.main()
