from __future__ import annotations

import unittest

from lizhi_agent.actions import MainActionType, SquadActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, PlayerState, RouteEdge, Station, TaskInstance
from lizhi_agent.strategy import BaselineStrategy


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None

    def close(self) -> None:
        return None


class StrategyRouteResourceTest(unittest.TestCase):
    def make_strategy(self) -> BaselineStrategy:
        return BaselineStrategy("1001", StrategyConfig.default(), SilentLogger())

    def test_delivery_guard_scouts_next_route_node(self) -> None:
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
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_SCOUT)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S02")

    def test_ice_box_used_before_critical_when_score_run_started(self) -> None:
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

    def test_short_horse_used_while_moving_after_score_floor(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.MOVING,
                station="S02",
                target="S03",
                task_score_base=90,
                resources={"SHORT_HORSE": 1},
            ),
            edges=[RouteEdge(id="E1", start="S02", end="S03", distance=1), RouteEdge(id="E2", start="S03", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "SHORT_HORSE")

    def test_after_90_score_still_detours_for_high_value_task(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
                RouteEdge(id="E4", start="S01", end="S14", distance=2),
            ],
            tasks=[TaskInstance(id="rich-task", template="T08", target="S03", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_competitive_score_locks_delivery(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=130),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
                RouteEdge(id="E4", start="S01", end="S14", distance=2),
            ],
            tasks=[TaskInstance(id="rich-task", template="T08", target="S03", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_fast_horse_used_before_long_idle_route(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, resources={"FAST_HORSE": 1}),
            edges=[RouteEdge(id="E1", start="S01", end="S08", distance=3), RouteEdge(id="E2", start="S08", end="S14", distance=3)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "FAST_HORSE")

    def test_pre_move_horse_does_not_skip_fixed_process(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", task_score_base=130, resources={"FAST_HORSE": 1}),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            edges=[RouteEdge(id="E1", start="S02", end="S14", distance=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.PROCESS)


if __name__ == "__main__":
    unittest.main()
