from __future__ import annotations

import io
import unittest

from lizhi_agent.actions import MainActionType, SquadActionType, WindowCard
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, PlayerState, ResourceStock, RouteEdge, Station, TaskInstance, WindowState, parse_game_state
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
        self.assertNotIn("targetNodeId", action.main.to_action())

    def test_delivered_state_does_not_attach_window_card(self) -> None:
        state = GameState(
            frame=560,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.DELIVERED,
                station="S15",
                verified=True,
                delivered=True,
            ),
            windows=[WindowState(id="late-window", window_type="TASK", target="S15", active=True, my_turn=True, round_index=1)],
        )
        action = self.strategy.decide(state)
        self.assertIsNone(action.main)
        self.assertIsNone(action.window)
        self.assertEqual(action.to_actions(), [])

    def test_empty_node_process_fields_fall_back_to_gameplay_process_nodes(self) -> None:
        start = {
            "durationRound": 600,
            "map": {
                "gameplay": {
                    "roles": {"startNodeId": "S01", "gateNodeId": "S14"},
                    "processNodes": [{"nodeId": "S02", "processType": "TRANSFER", "processRound": 4}],
                }
            },
            "players": [{"playerId": "1001"}, {"playerId": "1002"}],
            "nodes": [{"nodeId": "S02", "processType": "", "processRound": 0}],
            "edges": [],
        }
        inquire = {
            "round": 1,
            "phase": "NORMAL",
            "players": [{"playerId": "1001", "state": "IDLE", "currentNodeId": "S02"}],
            "nodes": [{"nodeId": "S02", "processType": "", "processRound": 0}],
        }
        state = parse_game_state("1001", start, inquire)
        station = state.station("S02")
        self.assertIsNotNone(station)
        self.assertEqual(station.process_type, "TRANSFER")
        self.assertEqual(station.process_round, 4)

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

    def test_claim_priority_resource_before_score_floor(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=30),
            resources=[
                ResourceStock(station="S03", resource_type="BOAT_RIGHT", amount=1),
                ResourceStock(station="S03", resource_type="ICE_BOX", amount=1),
            ],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_move_towards_gate_after_score_floor(self) -> None:
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

    def test_learns_guard_block_target_from_recent_move(self) -> None:
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        first_action = self.strategy.decide(first)
        self.assertEqual(first_action.main.action, MainActionType.MOVE)
        self.assertEqual(first_action.main.to_action()["targetNodeId"], "S09")

        blocked = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "move_blocked_by_guard"}],
        )
        recovery = self.strategy.decide(blocked)
        self.assertEqual(recovery.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(recovery.main.to_action()["targetNodeId"], "S09")

    def test_guard_block_does_not_use_squad_weaken(self) -> None:
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.MOVE)

        blocked = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=2),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD"}],
        )
        recovery = self.strategy.decide(blocked)
        self.assertIsNotNone(recovery.main)
        self.assertEqual(recovery.main.action, MainActionType.FORCED_PASS)
        self.assertNotEqual(recovery.squad.action if recovery.squad else None, SquadActionType.SQUAD_WEAKEN)

    def test_uses_official_permit_to_unblock_guard(self) -> None:
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.MOVE)

        blocked = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, resources={"OFFICIAL_PERMIT": 1}, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD"}],
        )
        action = self.strategy.decide(blocked)
        # OFFICIAL_PERMIT is a YAN_DIE window card cost, not a USE_RESOURCE.
        # Guarded S09 is the only route — must FORCED_PASS through.
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_uses_pass_token_when_no_official_permit(self) -> None:
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.MOVE)

        blocked = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, resources={"PASS_TOKEN": 1}, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD"}],
        )
        action = self.strategy.decide(blocked)
        # PASS_TOKEN is a YAN_DIE window card cost, not a USE_RESOURCE.
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_guard_block_sends_forced_pass_when_no_alternate(self) -> None:
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.MOVE)

        blocked = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD"}],
        )
        action = self.strategy.decide(blocked)
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_guard_block_learns_explicit_target_without_recent_move_memory(self) -> None:
        strategy = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger())
        blocked = GameState(
            frame=208,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=80, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S07", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S09"}],
        )
        action = strategy.decide(blocked)
        self.assertNotEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_guard_block_event_learns_without_action_result(self) -> None:
        strategy = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger())
        blocked = GameState(
            frame=208,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=80, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S07", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
            events=[{"type": "MOVE_BLOCKED_BY_GUARD", "payload": {"playerId": "1001", "targetNodeId": "S09"}}],
        )
        action = strategy.decide(blocked)
        self.assertNotEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_guard_block_prefers_alternate_route(self) -> None:
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09"), "S10": Station(id="S10")},
            edges=[
                RouteEdge(id="E1", start="S08", end="S09", distance=1),
                RouteEdge(id="E2", start="S09", end="S14", distance=1),
                RouteEdge(id="E3", start="S08", end="S10", distance=2),
                RouteEdge(id="E4", start="S10", end="S14", distance=1),
            ],
        )
        self.assertEqual(self.strategy.decide(first).main.to_action()["targetNodeId"], "S09")

        blocked = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09"), "S10": Station(id="S10")},
            edges=[
                RouteEdge(id="E1", start="S08", end="S09", distance=1),
                RouteEdge(id="E2", start="S09", end="S14", distance=1),
                RouteEdge(id="E3", start="S08", end="S10", distance=2),
                RouteEdge(id="E4", start="S10", end="S14", distance=1),
            ],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD"}],
        )
        action = self.strategy.decide(blocked)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S10")

    def test_live_guard_trap_prefers_forbidden_alternate_route(self) -> None:
        state = GameState(
            frame=167,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=0, squad_available=7),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S09", good_fruit=1),
            stations={"S05": Station(id="S05"), "S07": Station(id="S07"), "S08": Station(id="S08"), "S09": Station(id="S09"), "S10": Station(id="S10"), "S11": Station(id="S11"), "S12": Station(id="S12"), "S14": Station(id="S14")},
            edges=[
                RouteEdge(id="E04", start="S07", end="S09", route_type="ROAD", distance=46),
                RouteEdge(id="E05", start="S09", end="S10", route_type="ROAD", distance=40),
                RouteEdge(id="E06", start="S10", end="S11", route_type="ROAD", distance=36),
                RouteEdge(id="E07", start="S11", end="S12", route_type="ROAD", distance=20),
                RouteEdge(id="E08", start="S12", end="S14", route_type="ROAD", distance=25),
                RouteEdge(id="E13", start="S05", end="S07", route_type="BRANCH", distance=46),
                RouteEdge(id="E20", start="S07", end="S08", route_type="MOUNTAIN", distance=42),
                RouteEdge(id="E17", start="S08", end="S10", route_type="BRANCH", distance=46),
                RouteEdge(id="E22", start="S08", end="S09", route_type="BRANCH", distance=64),
            ],
            tasks=[TaskInstance(id="T_10", template="T13", target="S12", score=15, process_frames=5)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S08")

    def test_waiting_at_station_still_plans_next_move(self) -> None:
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.WAITING, station="S08", target="S14", task_score_base=90),
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_waiting_on_route_edge_keeps_system_wait(self) -> None:
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.WAITING,
                station="S08",
                target="S09",
                route_edge_id="E1",
                task_score_base=90,
            ),
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        action = self.strategy.decide(state)
        self.assertIsNone(action.main)

    def test_window_card_does_not_block_fixed_process(self) -> None:
        state = GameState(
            frame=57,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", guard_points=4),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            windows=[WindowState(id="contest-process", window_type="TASK", target="S02", active=True, my_turn=True, round_index=1)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.PROCESS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")
        self.assertIsNotNone(action.window)

    def test_pending_process_waits_briefly_for_server_to_enter_processing(self) -> None:
        first = GameState(
            frame=57,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", guard_points=4),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            windows=[WindowState(id="contest-process", window_type="TASK", target="S02", active=True, my_turn=True, round_index=1)],
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.PROCESS)

        second = GameState(
            frame=58,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", guard_points=4),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            windows=[WindowState(id="contest-process", window_type="TASK", target="S02", active=True, my_turn=True, round_index=2)],
        )
        action = self.strategy.decide(second)
        self.assertIsNone(action.main)
        self.assertIsNotNone(action.window)

    def test_unconfirmed_pending_process_retries_instead_of_waiting_forever(self) -> None:
        first = GameState(
            frame=57,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.PROCESS)

        stalled = GameState(
            frame=61,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
        )
        action = self.strategy.decide(stalled)
        self.assertEqual(action.main.action, MainActionType.PROCESS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_confirmed_pending_process_still_waits(self) -> None:
        first = GameState(
            frame=57,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.PROCESS)

        processing = GameState(
            frame=61,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.PROCESSING,
                station="S02",
                current_process={"type": "TRANSFER", "targetNodeId": "S02"},
            ),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
        )
        action = self.strategy.decide(processing)
        self.assertIsNone(action.main)

    def test_object_busy_sets_short_cooldown(self) -> None:
        first = GameState(
            frame=57,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.PROCESS)

        busy = GameState(
            frame=58,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            action_results=[{"playerId": "1001", "action": "PROCESS", "accepted": False, "code": "OBJECT_BUSY", "targetNodeId": "S02"}],
        )
        action = self.strategy.decide(busy)
        self.assertTrue(action.main is None or action.main.action != MainActionType.PROCESS)

    def test_process_complete_allows_leaving_fixed_node(self) -> None:
        first = GameState(
            frame=57,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            edges=[RouteEdge(id="E1", start="S02", end="S14", distance=1)],
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.PROCESS)

        completed = GameState(
            frame=62,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", task_score_base=90),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            edges=[RouteEdge(id="E1", start="S02", end="S14", distance=1)],
            events=[{"eventType": "PROCESS_COMPLETE", "playerId": "1001", "nodeId": "S02"}],
        )
        action = self.strategy.decide(completed)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_window_card_does_not_block_move(self) -> None:
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, guard_points=4),
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
            windows=[WindowState(id="contest-move", window_type="TASK", target="S02", active=True, my_turn=True, round_index=1)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")
        self.assertIsNotNone(action.window)

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

    def test_station_stall_escapes_current_task_loop(self) -> None:
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=30),
            edges=[RouteEdge(id="E1", start="S03", end="S14", distance=1)],
            tasks=[TaskInstance(id="task-loop", template="T01", target="S03", score=30, process_frames=3)],
        )
        self.assertEqual(self.strategy.decide(first).main.action, MainActionType.CLAIM_TASK)

        stalled = GameState(
            frame=119,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=30),
            edges=[RouteEdge(id="E1", start="S03", end="S14", distance=1)],
            tasks=[TaskInstance(id="task-loop", template="T01", target="S03", score=30, process_frames=3)],
        )
        action = self.strategy.decide(stalled)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_task_score_120_locks_delivery_midgame(self) -> None:
        state = GameState(
            frame=240,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S09", task_score_base=120),
            edges=[RouteEdge(id="E1", start="S09", end="S14", distance=1)],
            tasks=[TaskInstance(id="greedy-task", template="T08", target="S09", score=45, process_frames=4)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_task_condition_failure_cools_same_template(self) -> None:
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S09", task_score_base=30, resources={"SHORT_HORSE": 1}),
            edges=[RouteEdge(id="E1", start="S09", end="S14", distance=1)],
            tasks=[
                TaskInstance(id="task-a", template="T06", target="S09", score=30, process_frames=3),
                TaskInstance(id="task-b", template="T06", target="S09", score=30, process_frames=3),
            ],
        )
        self.assertEqual(self.strategy.decide(first).main.to_action()["taskId"], "task-a")

        rejected = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S09", task_score_base=30, resources={"SHORT_HORSE": 1}),
            edges=[RouteEdge(id="E1", start="S09", end="S14", distance=1)],
            tasks=[
                TaskInstance(id="task-a", template="T06", target="S09", score=30, process_frames=3),
                TaskInstance(id="task-b", template="T06", target="S09", score=30, process_frames=3),
            ],
            action_results=[{"playerId": "1001", "action": "CLAIM_TASK", "accepted": False, "code": "TASK_CONDITION_NOT_MET", "taskId": "task-a"}],
        )
        action = self.strategy.decide(rejected)
        self.assertNotEqual(action.main.to_action().get("taskId"), "task-b")

    def test_scout_skips_targets_that_marker_will_expire_before_arrival(self) -> None:
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", squad_available=2),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=20),
                RouteEdge(id="E2", start="S02", end="S03", distance=20),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="task-far", template="T01", target="S03", score=45, process_frames=3)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")
        self.assertIsNone(action.squad)

    def test_uses_fast_horse_before_long_route_move(self) -> None:
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=95, resources={"FAST_HORSE": 1}),
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=5), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "FAST_HORSE")

    def test_sets_guard_on_key_chokepoint_even_when_it_is_our_mainline(self) -> None:
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=95, good_fruit=95),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S08", task_score_base=90),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        action = self.strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.SET_GUARD)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")
        self.assertEqual(action.main.to_action()["extraGoodFruit"], 1)

    def test_repeated_window_suppresses_after_hard_limit(self) -> None:
        window = WindowState(
            id="contest-1",
            window_type="TASK",
            target="S03",
            task_id="task-loop",
            active=True,
            my_turn=True,
            round_index=1,
        )
        base = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", guard_points=1),
            windows=[window],
        )
        self.assertNotEqual(self.strategy.decide(base).window.card, WindowCard.ABSTAIN)
        self.assertNotEqual(self.strategy.decide(base).window.card, WindowCard.ABSTAIN)
        self.assertIsNotNone(self.strategy.decide(base).window)
        self.assertIsNone(self.strategy.decide(base).window)

    def test_opening_window_uses_mixed_cards(self) -> None:
        cards = set()
        for index in range(8):
            strategy = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger())
            state = GameState(
                frame=30,
                phase="NORMAL",
                player_id="1001",
                me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", guard_points=4, good_fruit=100, freshness=100),
                windows=[WindowState(id=f"contest-{index}", window_type="TASK", target="S02", task_id="task-open", active=True, my_turn=True, round_index=1)],
            )
            cards.add(strategy.decide(state).window.card)
        self.assertIn(WindowCard.BING_ZHENG, cards)
        self.assertIn(WindowCard.XIAN_GONG, cards)

    def test_late_high_value_window_no_longer_forces_bing_zheng(self) -> None:
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", guard_points=4),
            windows=[WindowState(id="contest-late", window_type="TASK", target="S02", task_id="task-late", active=True, my_turn=True, round_index=1)],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.XIAN_GONG)

    def test_window_counters_revealed_bing_zheng_with_xian_gong(self) -> None:
        state = GameState(
            frame=90,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S02", good_fruit=95, freshness=95),
            windows=[
                WindowState(
                    id="contest-counter",
                    window_type="TASK",
                    target="S02",
                    active=True,
                    my_turn=True,
                    round_index=2,
                    raw={"cards": {"RED": "BING_ZHENG", "BLUE": "BING_ZHENG"}},
                )
            ],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.XIAN_GONG)

    def test_window_uses_document_to_counter_qiang_xing(self) -> None:
        state = GameState(
            frame=90,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.IDLE,
                station="S02",
                resources={"PASS_TOKEN": 1},
            ),
            windows=[
                WindowState(
                    id="contest-counter-doc",
                    window_type="PASS",
                    target="S02",
                    active=True,
                    my_turn=True,
                    round_index=2,
                    raw={"cards": {"RED": "BING_ZHENG", "BLUE": "QIANG_XING"}},
                )
            ],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.YAN_DIE)

    def test_window_draws_revealed_xian_gong_when_no_qiang_xing(self) -> None:
        state = GameState(
            frame=150,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S02", good_fruit=95, freshness=95, guard_points=4),
            windows=[
                WindowState(
                    id="contest-counter-xian",
                    window_type="TASK",
                    target="S02",
                    active=True,
                    my_turn=True,
                    round_index=2,
                    raw={"cards": {"RED": "BING_ZHENG", "BLUE": "XIAN_GONG"}},
                )
            ],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.XIAN_GONG)

    def test_window_uses_qiang_xing_to_counter_xian_gong_when_available(self) -> None:
        state = GameState(
            frame=150,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.IDLE,
                station="S02",
                good_fruit=95,
                freshness=95,
                guard_points=4,
                resources={"FAST_HORSE": 1},
            ),
            windows=[
                WindowState(
                    id="contest-counter-xian-horse",
                    window_type="TASK",
                    target="S02",
                    active=True,
                    my_turn=True,
                    round_index=2,
                    raw={"cards": {"RED": "BING_ZHENG", "BLUE": "XIAN_GONG"}},
                )
            ],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.QIANG_XING)

    def test_window_xian_gong_streak_does_not_bing_zheng(self) -> None:
        first = GameState(
            frame=150,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S02", good_fruit=95, freshness=95, guard_points=4),
            windows=[WindowState(id="contest-streak-1", window_type="TASK", target="S02", active=True, my_turn=True, round_index=2, raw={"cards": {"BLUE": "XIAN_GONG"}})],
        )
        second = GameState(
            frame=151,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S02", good_fruit=95, freshness=95, guard_points=4),
            windows=[WindowState(id="contest-streak-2", window_type="TASK", target="S02", active=True, my_turn=True, round_index=2, raw={"cards": {"BLUE": "XIAN_GONG"}})],
        )
        self.assertEqual(self.strategy.decide(first).window.card, WindowCard.XIAN_GONG)
        self.assertNotEqual(self.strategy.decide(second).window.card, WindowCard.BING_ZHENG)

    def test_window_counters_direct_last_card_fields(self) -> None:
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S02", guard_points=1),
            windows=[
                WindowState(
                    id="contest-direct-counter",
                    window_type="TASK",
                    target="S02",
                    active=True,
                    my_turn=True,
                    round_index=2,
                    raw={"redPlayerId": "1001", "bluePlayerId": "1002", "blueCard": "QIANG_XING"},
                )
            ],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.BING_ZHENG)

    def test_low_value_window_does_not_spend_good_fruit(self) -> None:
        state = GameState(
            frame=30,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", guard_points=0, good_fruit=100, freshness=100),
            windows=[WindowState(id="contest-low-resource", window_type="RESOURCE", resource_type="BOAT_RIGHT", target="S02", active=True, my_turn=True, round_index=1)],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.ABSTAIN)

    def test_low_value_window_preserves_guard_points_even_when_affordable(self) -> None:
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", guard_points=2, good_fruit=92, freshness=92),
            windows=[WindowState(id="contest-low-guard", window_type="RESOURCE", resource_type="BOAT_RIGHT", target="S02", active=True, my_turn=True, round_index=1)],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.ABSTAIN)

    def test_symmetric_bots_pick_different_opening_window_cards(self) -> None:
        window = WindowState(
            id="contest-symmetric",
            window_type="TASK",
            target="S02",
            task_id="task-hot",
            active=True,
            my_turn=True,
            round_index=1,
            raw={"redPlayerId": "1001", "bluePlayerId": "1002", "totalRounds": 3},
        )
        red = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S02", guard_points=2, good_fruit=96, freshness=96),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S02", guard_points=2, good_fruit=96, freshness=96),
            windows=[window],
            tasks=[TaskInstance(id="task-hot", template="T08", target="S02", score=45, process_frames=4)],
        )
        blue = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1002",
            me=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S02", guard_points=2, good_fruit=96, freshness=96),
            opponent=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S02", guard_points=2, good_fruit=96, freshness=96),
            windows=[window],
            tasks=[TaskInstance(id="task-hot", template="T08", target="S02", score=45, process_frames=4)],
        )
        red_card = BaselineStrategy("1001", StrategyConfig.default(), SilentLogger()).decide(red).window.card
        blue_card = BaselineStrategy("1002", StrategyConfig.default(), SilentLogger()).decide(blue).window.card
        self.assertNotIn(WindowCard.ABSTAIN, {red_card, blue_card})
        self.assertIn(red_card, {WindowCard.BING_ZHENG, WindowCard.XIAN_GONG, WindowCard.QIANG_XING, WindowCard.YAN_DIE})
        self.assertIn(blue_card, {WindowCard.BING_ZHENG, WindowCard.XIAN_GONG, WindowCard.QIANG_XING, WindowCard.YAN_DIE})

    def test_window_abstains_only_when_lead_is_mathematically_safe(self) -> None:
        state = GameState(
            frame=250,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S02", guard_points=2, good_fruit=95, freshness=95),
            windows=[
                WindowState(
                    id="contest-safe-lead",
                    window_type="TASK",
                    target="S02",
                    task_id="task-safe",
                    active=True,
                    my_turn=True,
                    round_index=3,
                    red_point=2,
                    blue_point=0,
                    raw={"redPlayerId": "1001", "bluePlayerId": "1002", "totalRounds": 3},
                )
            ],
        )
        self.assertEqual(self.strategy.decide(state).window.card, WindowCard.ABSTAIN)


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
