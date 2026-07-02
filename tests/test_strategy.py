from __future__ import annotations

import io
import unittest

from lizhi_agent.actions import MainActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, PlayerState, ResourceStock, RouteEdge, Station, TaskInstance
from lizhi_agent.protocol import LengthPrefixedCodec
from lizhi_agent.strategy import BaselineStrategy


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None

    def close(self) -> None:
        return None


class MemoryStream:
    def __init__(self, initial: bytes = b"") -> None:
        self.reader = io.BytesIO(initial)
        self.writer = io.BytesIO()

    def read(self, size: int) -> bytes:
        return self.reader.read(size)

    def write(self, data: bytes) -> int:
        return self.writer.write(data)

    def flush(self) -> None:
        return None


class BaselineStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger())

    def test_deliver_when_at_terminal_verified(self) -> None:
        state = GameState(
            frame=500,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S15", verified=True),
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.DELIVER)

    def test_verify_when_at_gate_in_rush(self) -> None:
        state = GameState(
            frame=430,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S14", verified=False),
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.VERIFY_GATE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_claim_valuable_task_before_90_score(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=30),
            tasks=[TaskInstance(id="task-1", template="T01", target="S03", score=30, process_frames=3)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.main.to_action()["taskId"], "task-1")

    def test_claim_priority_resource(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=90),
            resources=[
                ResourceStock(station="S03", resource_type="BOAT_RIGHT", amount=1),
                ResourceStock(station="S03", resource_type="ICE_BOX", amount=1),
            ],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_move_towards_gate(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S14", distance=1),
            ],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_obstacle_uses_t04_instead_of_plain_clear(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90),
            stations={"S02": Station(id="S02", has_obstacle=True)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
            tasks=[TaskInstance(id="t04", template="T04", target="S02", score=30, process_frames=6)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.main.to_action()["taskId"], "t04")


class ProtocolCodecTest(unittest.TestCase):
    def test_length_prefixed_codec_roundtrip(self) -> None:
        stream = MemoryStream()
        codec = LengthPrefixedCodec(stream)
        codec.write_message({"msg_name": "action", "msg_data": {"round": 1, "actions": []}})
        raw = stream.writer.getvalue()
        self.assertTrue(raw[:5].isdigit())
        reader_codec = LengthPrefixedCodec(MemoryStream(raw))
        self.assertEqual(reader_codec.read_message()["msg_name"], "action")


if __name__ == "__main__":
    unittest.main()
