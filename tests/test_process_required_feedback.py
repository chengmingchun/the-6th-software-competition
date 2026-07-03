from __future__ import annotations

import unittest

from lizhi_agent.actions import MainActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, PlayerState, RouteEdge, Station, parse_game_state
from lizhi_agent.strategy import BaselineStrategy


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None

    def close(self) -> None:
        return None


class ProcessRequiredFeedbackTest(unittest.TestCase):
    def test_move_process_required_targets_current_station(self) -> None:
        start = {
            "durationRound": 600,
            "map": {"gameplay": {"roles": {"startNodeId": "S01", "gateNodeId": "S14"}}},
            "players": [{"playerId": "1001"}],
            "nodes": [{"nodeId": "S02", "processType": "TRANSFER", "processRound": 4}, {"nodeId": "S04"}],
            "edges": [{"edgeId": "E1", "fromNodeId": "S02", "toNodeId": "S04"}],
        }
        inquire = {
            "round": 56,
            "players": [{"playerId": "1001", "state": "IDLE", "currentNodeId": "S02"}],
            "nodes": [{"nodeId": "S02", "processType": "TRANSFER", "processRound": 4}, {"nodeId": "S04"}],
            "actionResults": [{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "PROCESS_REQUIRED", "targetNodeId": "S04"}],
        }
        state = parse_game_state("1001", start, inquire)
        self.assertEqual(state.action_results[0]["targetNodeId"], "S02")
        self.assertEqual(state.action_results[0]["rawTargetNodeId"], "S04")

    def test_process_required_bypasses_recent_object_busy_cooldown(self) -> None:
        strategy = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger())
        busy = GameState(
            frame=55,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            action_results=[{"playerId": "1001", "action": "PROCESS", "accepted": False, "code": "OBJECT_BUSY", "targetNodeId": "S02"}],
        )
        action = strategy.decide(busy)
        self.assertTrue(action.main is None or action.main.action != MainActionType.PROCESS)

        required = GameState(
            frame=56,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4), "S04": Station(id="S04")},
            edges=[RouteEdge(id="E1", start="S02", end="S04", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "PROCESS_REQUIRED", "targetNodeId": "S02"}],
        )
        action = strategy.decide(required)
        self.assertEqual(action.main.action, MainActionType.PROCESS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")


if __name__ == "__main__":
    unittest.main()
