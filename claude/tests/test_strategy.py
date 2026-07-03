from __future__ import annotations

import io
import unittest

from lizhi_agent.actions import MainActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, PlayerState, ResourceStock, RouteEdge, TaskInstance
from lizhi_agent.protocol import LengthPrefixedCodec
from lizhi_agent.strategy import FreshnessFirstStrategy


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


class FreshnessFirstStrategySmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = FreshnessFirstStrategy("1001", StrategyConfig.default(), SilentLogger())

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

    def test_claim_current_task_before_score_floor(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=30),
            tasks=[TaskInstance(id="task-1", template="T01", target="S03", score=30, process_frames=3)],
            edges=[
                RouteEdge(id="E1", start="S03", end="S14", distance=1),
                RouteEdge(id="E2", start="S14", end="S15", distance=1),
            ],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.main.to_action()["taskId"], "task-1")

    def test_claim_priority_resource_on_road_station(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=30),
            resources=[
                ResourceStock(station="S03", resource_type="BOAT_RIGHT", amount=1),
                ResourceStock(station="S03", resource_type="ICE_BOX", amount=1),
            ],
            edges=[
                RouteEdge(id="E1", start="S03", end="S14", distance=1),
                RouteEdge(id="E2", start="S14", end="S15", distance=1),
            ],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_roadmaster_moves_along_road_path_after_score_floor(self) -> None:
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

    def test_moving_state_keeps_system_wait(self) -> None:
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.MOVING,
                station="S08",
                target="S09",
                route_edge_id="E1",
                task_score_base=90,
            ),
        )
        action = self.strategy.decide(state)
        self.assertIsNone(action.main)


class ProtocolCodecSmokeTest(unittest.TestCase):
    def test_length_prefixed_codec_round_trip(self) -> None:
        stream = MemoryStream()
        codec = LengthPrefixedCodec(stream)
        codec.write_message({"msg_name": "ready", "msg_data": {"playerId": 1001}})
        written = stream.writer.getvalue()
        replay = LengthPrefixedCodec(MemoryStream(written))
        self.assertEqual(replay.read_message()["msg_name"], "ready")


if __name__ == "__main__":
    unittest.main()
