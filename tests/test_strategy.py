from __future__ import annotations

import io
import unittest

from lizhi_agent.actions import MainActionType, WindowCard
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import GameState, PlayerState, RouteEdge, TaskInstance, ResourceStock, ConvoyStatus, WindowState
from lizhi_agent.protocol import FrameCodec
from lizhi_agent.strategy import BaselineStrategy


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None


class BaselineStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = BaselineStrategy("p0", StrategyConfig.default(), SilentLogger())

    def test_deliver_when_at_s15_verified(self) -> None:
        state = GameState(
            frame=500,
            phase="RUSH",
            me=PlayerState(player_id="p0", status=ConvoyStatus.IDLE, station="S15", verified=True),
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.DELIVER)
        self.assertEqual(action.to_actions(), [{"action": "DELIVER"}])

    def test_verify_when_at_s14(self) -> None:
        state = GameState(
            frame=430,
            phase="RUSH",
            me=PlayerState(player_id="p0", status=ConvoyStatus.IDLE, station="S14", verified=False),
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.VERIFY_GATE)
        self.assertEqual(action.to_actions(), [{"action": "VERIFY_GATE", "targetNodeId": "S14"}])

    def test_claim_valuable_task_before_90_score(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            me=PlayerState(player_id="p0", status=ConvoyStatus.IDLE, station="S03", task_score_base=30),
            tasks=[TaskInstance(id="task-1", template="T01", target="S03", score=30, process_frames=3)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.to_actions(), [{"action": "CLAIM_TASK", "taskId": "task-1"}])

    def test_claim_priority_resource(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            me=PlayerState(player_id="p0", status=ConvoyStatus.IDLE, station="S03", task_score_base=90),
            resources=[
                ResourceStock(station="S03", resource_type="BOAT_RIGHT", amount=1),
                ResourceStock(station="S03", resource_type="ICE_BOX", amount=1),
            ],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_RESOURCE)
        self.assertEqual(action.to_actions(), [{"action": "CLAIM_RESOURCE", "targetNodeId": "S03", "resourceType": "ICE_BOX"}])

    def test_window_card_protocol_action(self) -> None:
        state = GameState(
            frame=120,
            phase="NORMAL",
            me=PlayerState(player_id="p0", status=ConvoyStatus.IDLE, station="S03", guard_points=1),
            windows=[WindowState(id="C1", window_type="RESOURCE", target="S03", active=True, my_turn=True)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.window.card, WindowCard.BING_ZHENG)
        self.assertEqual(action.to_actions(), [{"action": "WINDOW_CARD", "contestId": "C1", "card": "BING_ZHENG"}])

    def test_move_towards_s14(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            me=PlayerState(player_id="p0", status=ConvoyStatus.IDLE, station="S01", task_score_base=90),
            edges=[RouteEdge(start="S01", end="S02"), RouteEdge(start="S02", end="S14")],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.to_actions(), [{"action": "MOVE", "targetNodeId": "S02"}])


class FrameCodecTest(unittest.TestCase):
    def test_length_prefixed_round_trip(self) -> None:
        payload = {"msg_name": "action", "msg_data": {"matchId": "m1", "round": 1, "playerId": 1001, "actions": []}}
        encoded = FrameCodec.encode(payload)
        self.assertEqual(len(encoded[:5]), 5)
        self.assertTrue(encoded[:5].isdigit())
        decoded = FrameCodec.read(io.BytesIO(encoded))
        self.assertEqual(decoded, payload)


if __name__ == "__main__":
    unittest.main()
