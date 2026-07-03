from __future__ import annotations

import unittest

from lizhi_agent.actions import MainActionType, SquadActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, PlayerState, ResourceStock, RouteEdge, Station
from lizhi_agent.strategy import RoadMasterStrategy


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None

    def close(self) -> None:
        return None


class RoadMasterRouteResourceSmokeTest(unittest.TestCase):
    def make_strategy(self) -> RoadMasterStrategy:
        return RoadMasterStrategy("1001", StrategyConfig.default(), SilentLogger())

    def test_fixed_process_on_road_node(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=57,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.PROCESS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_process_complete_allows_next_road_move(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=57,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            edges=[RouteEdge(id="E1", start="S02", end="S03", distance=1)],
        )
        self.assertEqual(strategy.decide(first).main.action, MainActionType.PROCESS)

        completed = GameState(
            frame=62,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", task_score_base=90),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            edges=[RouteEdge(id="E1", start="S02", end="S03", distance=1)],
            events=[{"type": "PROCESS_COMPLETE", "playerId": "1001", "nodeId": "S02"}],
        )
        action = strategy.decide(completed)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S03")

    def test_scouts_next_valuable_road_node(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, squad_available=2),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_SCOUT)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S02")

    def test_ice_box_used_early_to_preserve_freshness(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=70,
                task_score_base=45,
                resources={"ICE_BOX": 1},
            ),
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_horse_used_while_moving(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.MOVING,
                station="S07",
                route_edge_id="E1",
                task_score_base=90,
                resources={"SHORT_HORSE": 1},
            ),
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "SHORT_HORSE")


if __name__ == "__main__":
    unittest.main()
