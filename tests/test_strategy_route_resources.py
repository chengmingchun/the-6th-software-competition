from __future__ import annotations

import unittest

from lizhi_agent.actions import ActionBundle, MainAction, MainActionType, SquadActionType
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import ConvoyStatus, GameState, PlayerState, ResourceStock, RouteEdge, Station, TaskInstance, WeatherState
from lizhi_agent.strategy import BaselineStrategy


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None

    def close(self) -> None:
        return None


class RecordingLogger(DecisionLogger):
    def __init__(self) -> None:
        self.records = []

    def info(self, event: str, **fields):
        self.records.append((event, fields))

    def close(self) -> None:
        return None


class StrategyRouteResourceTest(unittest.TestCase):
    def make_strategy(self) -> BaselineStrategy:
        return BaselineStrategy("1001", StrategyConfig.default(), SilentLogger())

    def test_delivery_guard_does_not_scout_plain_pass_through_node(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, squad_available=6),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")
        self.assertIsNone(action.squad)

    def test_scout_preserves_key_pass_rescue_reserve(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, squad_available=5),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="rich-task", template="T08", target="S03", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertIsNone(action.squad)

    def test_squad_scouts_reachable_task_node_instead_of_plain_next_hop(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, squad_available=6),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="rich-task", template="T08", target="S03", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_SCOUT)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S03")

    def test_moving_uses_horse_and_squad_weaken_when_both_are_legal(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.MOVING,
                station="S09",
                target="S10",
                task_score_base=100,
                resources={"FAST_HORSE": 1},
                squad_available=2,
            ),
            stations={"S10": Station(id="S10", guard_owner="BLUE", guard_defense=4)},
            edges=[RouteEdge(id="E1", start="S09", end="S10", distance=4), RouteEdge(id="E2", start="S10", end="S14", distance=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "FAST_HORSE")
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_WEAKEN)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S10")

    def test_claiming_current_task_scouts_next_valuable_task(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", task_score_base=60, squad_available=6),
            edges=[
                RouteEdge(id="E1", start="S02", end="S03", distance=1),
                RouteEdge(id="E2", start="S03", end="S14", distance=1),
            ],
            tasks=[
                TaskInstance(id="current-task", template="T01", target="S02", score=30, process_frames=3),
                TaskInstance(id="next-task", template="T08", target="S03", score=45, process_frames=4),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.main.to_action()["taskId"], "current-task")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_SCOUT)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S03")

    def test_t04_routes_to_adjacent_claim_station(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=60,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", task_score_base=0),
            edges=[
                RouteEdge(id="D", start="S02", end="S14", distance=2),
                RouteEdge(id="A", start="S02", end="S03", distance=1),
                RouteEdge(id="B", start="S03", end="S06", distance=1),
                RouteEdge(id="C", start="S03", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="clear-s06", template="T04", target="S06", score=30, process_frames=6)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S03")

    def test_t04_claims_from_adjacent_station(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=70,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=0),
            edges=[RouteEdge(id="B", start="S03", end="S06", distance=1), RouteEdge(id="C", start="S03", end="S14", distance=1)],
            tasks=[TaskInstance(id="clear-s06", template="T04", target="S06", score=30, process_frames=6)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.main.to_action()["taskId"], "clear-s06")

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

    def test_moving_state_uses_horse_resource_allowed_by_protocol(self) -> None:
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
        self.assertIsNone(action.squad)

    def test_moving_state_can_send_squad_weaken_to_target_guard(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=300,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.MOVING,
                station="S09",
                target="S10",
                task_score_base=90,
                squad_available=2,
            ),
            stations={"S10": Station(id="S10", guard_owner="BLUE", guard_defense=4)},
            edges=[RouteEdge(id="E1", start="S09", end="S10", distance=1), RouteEdge(id="E2", start="S10", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertIsNone(action.main)
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_WEAKEN)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S10")

    def test_squad_weaken_invalid_action_type_does_not_repeat_loop(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=523,
            phase="RUSH",

            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.MOVING,
                station="S13",
                target="S14",
                task_score_base=120,
                squad_available=2,
            ),
            stations={"S14": Station(id="S14", guard_owner="BLUE", guard_defense=2)},
            edges=[RouteEdge(id="E1", start="S13", end="S14", distance=1), RouteEdge(id="E2", start="S14", end="S15", distance=1)],
        )
        first_action = strategy.decide(first)
        self.assertIsNotNone(first_action.squad)
        self.assertEqual(first_action.squad.action, SquadActionType.SQUAD_WEAKEN)
        self.assertEqual(first_action.squad.to_action()["targetNodeId"], "S14")

        rejected = GameState(
            frame=524,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.MOVING,
                station="S13",
                target="S14",
                task_score_base=120,
                squad_available=2,
            ),
            stations={"S14": Station(id="S14", guard_owner="BLUE", guard_defense=2)},
            edges=first.edges,
            action_results=[{"playerId": "1001", "action": "SQUAD_WEAKEN", "accepted": False, "code": "INVALID_ACTION_TYPE", "nodeId": "S13"}],
        )
        retry = strategy.decide(rejected)
        self.assertNotEqual(retry.squad.action if retry.squad else None, SquadActionType.SQUAD_WEAKEN)

    def test_moving_state_does_not_weaken_from_learned_guard_without_public_guard(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=360,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.MOVING,
                station="S09",
                target="S10",
                task_score_base=70,
                squad_available=2,
            ),
            stations={"S10": Station(id="S10")},
            edges=[RouteEdge(id="E1", start="S09", end="S10", distance=1), RouteEdge(id="E2", start="S10", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "node": "S10"}],
        )
        action = strategy.decide(state)
        self.assertIsNone(action.main)
        self.assertIsNone(action.squad)

    def test_moving_target_obstacle_uses_squad_clear_not_weaken(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=360,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.MOVING,
                station="S09",
                target="S10",
                task_score_base=70,
                squad_available=2,
            ),
            stations={"S10": Station(id="S10", has_obstacle=True, guard_owner="BLUE", guard_defense=4)},
            edges=[RouteEdge(id="E1", start="S09", end="S10", distance=1), RouteEdge(id="E2", start="S10", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertIsNone(action.main)
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_CLEAR)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S10")

    def test_moving_low_defense_guard_preserves_last_rescue_squad(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=300,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.MOVING,
                station="S09",
                target="S10",
                task_score_base=90,
                squad_available=4,
            ),
            stations={"S10": Station(id="S10", guard_owner="BLUE", guard_defense=1)},
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", distance=1),
                RouteEdge(id="E2", start="S10", end="S13", distance=1),
                RouteEdge(id="E3", start="S13", end="S14", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertIsNone(action.main)
        self.assertIsNone(action.squad)

    def test_remote_obstacle_clear_preserves_squad_for_uncrossed_key_pass(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=320,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.IDLE,
                station="S10",
                task_score_base=70,
                squad_available=4,
            ),
            stations={"S11": Station(id="S11", has_obstacle=True), "S13": Station(id="S13", node_type="KEY_PASS")},
            edges=[
                RouteEdge(id="E1", start="S10", end="S11", distance=1),
                RouteEdge(id="E2", start="S11", end="S13", distance=1),
                RouteEdge(id="E3", start="S13", end="S14", distance=1),
            ],
        )
        self.assertFalse(strategy._can_spend_squad(state, SquadActionType.SQUAD_CLEAR, "blocked_route_obstacle"))

    def test_rush_protect_used_before_gate_to_preserve_freshness(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=430,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S10",
                freshness=82,
                task_score_base=120,
                rush_tactic_used_count=0,
            ),
            edges=[RouteEdge(id="E1", start="S10", end="S14", distance=3)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.RUSH_PROTECT)

    def test_rush_speed_beats_protect_when_deadline_is_tight(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=570,
            max_frame=600,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S10",
                freshness=95,
                good_fruit=95,
                task_score_base=120,
                rush_tactic_used_count=0,
            ),
            edges=[RouteEdge(id="E1", start="S10", end="S14", distance=10)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.RUSH_SPEED)

    def test_rush_uses_horse_before_protect_when_available(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=500,
            max_frame=600,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S10",
                freshness=95,
                task_score_base=120,
                rush_tactic_used_count=0,
                resources={"FAST_HORSE": 1},
            ),
            edges=[RouteEdge(id="E1", start="S10", end="S14", distance=6)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "FAST_HORSE")

    def test_rush_protect_preempts_horse_when_freshness_is_under_pressure(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=500,
            max_frame=600,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S10",
                freshness=87,
                task_score_base=120,
                rush_tactic_used_count=0,
                resources={"FAST_HORSE": 1},
            ),
            edges=[RouteEdge(id="E1", start="S10", end="S14", distance=6)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.RUSH_PROTECT)

    def test_rush_protect_does_not_skip_fixed_process(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=430,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S11",
                freshness=82,
                task_score_base=120,
                rush_tactic_used_count=0,
            ),
            stations={"S11": Station(id="S11", process_type="PASS_TRANSFER", process_round=4)},
            edges=[RouteEdge(id="E1", start="S11", end="S14", distance=3)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.PROCESS)

    def test_gate_verify_keeps_break_order_priority(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=430,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S14",
                freshness=82,
                task_score_base=120,
                rush_tactic_used_count=0,
            ),
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.VERIFY_GATE)
        self.assertEqual(action.main.to_action()["rushTactic"], "BREAK_ORDER")

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

    def test_competitive_score_still_chases_high_value_task(self) -> None:
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
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_greed_score_locks_delivery(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=180),
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

    def test_rejected_fast_horse_uses_recent_attempt_cooldown(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, resources={"FAST_HORSE": 1}),
            edges=[RouteEdge(id="E1", start="S01", end="S08", distance=3), RouteEdge(id="E2", start="S08", end="S14", distance=3)],
        )
        self.assertEqual(strategy.decide(first).main.to_action()["resourceType"], "FAST_HORSE")

        rejected = GameState(
            frame=181,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, resources={"FAST_HORSE": 1}),
            edges=first.edges,
            action_results=[{"playerId": "1001", "action": "USE_RESOURCE", "accepted": False, "code": "RESOURCE_USE_NOT_ALLOWED"}],
        )
        action = strategy.decide(rejected)
        self.assertFalse(action.main and action.main.action == MainActionType.USE_RESOURCE and action.main.to_action().get("resourceType") == "FAST_HORSE")

    def test_station_task_takes_priority_over_pre_move_horse(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=130, resources={"FAST_HORSE": 1}),
            edges=[RouteEdge(id="E1", start="S03", end="S14", distance=6)],
            tasks=[TaskInstance(id="station-task", template="T08", target="S03", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.main.to_action()["taskId"], "station-task")

    def test_t06_requires_horse_resource_before_claim(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S09", task_score_base=30),
            edges=[RouteEdge(id="E1", start="S09", end="S14", distance=1)],
            tasks=[TaskInstance(id="horse-task", template="T06", target="S09", score=30, process_frames=5)],
        )
        action = strategy.decide(state)
        self.assertFalse(action.main.action == MainActionType.CLAIM_TASK and action.main.to_action().get("taskId") == "horse-task")

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

    def test_high_score_waits_on_ice_box_above_pressure_threshold(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=89,
                task_score_base=170,
                resources={"ICE_BOX": 1},
            ),
        )
        action = strategy.decide(state)
        self.assertTrue(action.main is None or action.main.action != MainActionType.USE_RESOURCE)

    def test_target_score_waits_on_ice_box_at_eighty_eight_freshness_without_pressure(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=88,
                task_score_base=90,
                resources={"ICE_BOX": 1},
            ),
        )
        action = strategy.decide(state)
        self.assertTrue(action.main is None or action.main.action != MainActionType.USE_RESOURCE)

    def test_target_score_waits_on_ice_box_before_freshness_gap_opens(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=95,
                task_score_base=90,
                resources={"ICE_BOX": 1},
            ),
        )
        action = strategy.decide(state)
        self.assertTrue(action.main is None or action.main.action != MainActionType.USE_RESOURCE)

    def test_high_score_delivery_lock_uses_ice_box_at_eighty_eight_freshness(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=240,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S08",
                freshness=88,
                task_score_base=120,
                resources={"ICE_BOX": 1},
            ),
            edges=[
                RouteEdge(id="E1", start="S08", end="S09", distance=4),
                RouteEdge(id="E2", start="S09", end="S14", distance=4),
                RouteEdge(id="E3", start="S14", end="S15", distance=2),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_rush_low_score_uses_spare_ice_box_to_protect_delivery_quality(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=413,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S13",
                freshness=85.9,
                task_score_base=30,
                resources={"ICE_BOX": 1},
            ),
            stations={"S13": Station(id="S13", process_type="PALACE_TRANSFER", process_round=5)},
            edges=[
                RouteEdge(id="E1", start="S13", end="S14", distance=1),
                RouteEdge(id="E2", start="S14", end="S15", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_normal_midgame_does_not_use_ice_box_at_eighty_eight_point_six(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=88.6,
                task_score_base=60,
                resources={"ICE_BOX": 1},
            ),
        )
        action = strategy.decide(state)
        self.assertTrue(action.main is None or action.main.action != MainActionType.USE_RESOURCE)

    def test_target_score_uses_ice_box_at_eighty_two_freshness(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=82,
                task_score_base=90,
                resources={"ICE_BOX": 1},
            ),
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_hot_weather_low_score_with_time_does_not_use_ice_box(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=90,
                task_score_base=20,
                resources={"ICE_BOX": 1},
            ),
            edges=[
                RouteEdge(id="E1", start="S03", end="S14", distance=2),
                RouteEdge(id="E2", start="S14", end="S15", distance=2),
            ],
            weather=WeatherState(active_types=("HOT",)),
        )
        action = strategy.decide(state)
        self.assertTrue(action.main is None or action.main.action != MainActionType.USE_RESOURCE)

    def test_high_score_prioritizes_reachable_ice_box_before_delivery_guard(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", freshness=90, task_score_base=120),
            edges=[
                RouteEdge(id="D1", start="S01", end="S14", distance=10),
                RouteEdge(id="D2", start="S14", end="S15", distance=1),
                RouteEdge(id="I1", start="S01", end="S02", distance=3),
                RouteEdge(id="I2", start="S02", end="S14", distance=10),
            ],
            resources=[ResourceStock(station="S02", resource_type="ICE_BOX", amount=1, claim_frames=2)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_high_score_skips_ice_box_when_delivery_deadline_is_hard(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=560,
            max_frame=602,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", freshness=90, task_score_base=120),
            edges=[
                RouteEdge(id="D1", start="S01", end="S14", distance=10),
                RouteEdge(id="D2", start="S14", end="S15", distance=1),
                RouteEdge(id="I1", start="S01", end="S02", distance=3),
                RouteEdge(id="I2", start="S02", end="S14", distance=10),
            ],
            resources=[ResourceStock(station="S02", resource_type="ICE_BOX", amount=1, claim_frames=2)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_verified_ice_box_detour_uses_terminal_objective(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S10", verified=True, freshness=90, task_score_base=120),
            edges=[
                RouteEdge(id="DG", start="S10", end="S14", distance=1),
                RouteEdge(id="DT", start="S10", end="S15", distance=10),
                RouteEdge(id="I1", start="S10", end="S02", distance=1),
                RouteEdge(id="I2", start="S02", end="S14", distance=20),
                RouteEdge(id="I3", start="S02", end="S15", distance=10),
            ],
            resources=[ResourceStock(station="S02", resource_type="ICE_BOX", amount=1, claim_frames=2)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_hot_weather_allows_larger_ice_box_detour(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", freshness=94, task_score_base=90),
            edges=[
                RouteEdge(id="D1", start="S01", end="S14", distance=10),
                RouteEdge(id="D2", start="S14", end="S15", distance=1),
                RouteEdge(id="I1", start="S01", end="S02", distance=4),
                RouteEdge(id="I2", start="S02", end="S14", distance=14),
            ],
            resources=[ResourceStock(station="S02", resource_type="ICE_BOX", amount=1, claim_frames=2)],
            weather=WeatherState(active_types=("HOT",)),
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_mountain_route_allows_larger_ice_box_detour(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", freshness=94, task_score_base=90),
            edges=[
                RouteEdge(id="D1", start="S01", end="S14", route_type="MOUNTAIN", distance=7),
                RouteEdge(id="D2", start="S14", end="S15", distance=1),
                RouteEdge(id="I1", start="S01", end="S02", distance=4),
                RouteEdge(id="I2", start="S02", end="S14", distance=20),
            ],
            resources=[ResourceStock(station="S02", resource_type="ICE_BOX", amount=1, claim_frames=2)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_urgent_station_resource_before_task(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=45, freshness=97),
            resources=[ResourceStock(station="S03", resource_type="ICE_BOX", amount=1, claim_frames=2)],
            tasks=[TaskInstance(id="task-local", template="T01", target="S03", score=30, process_frames=3)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_resource_intent_and_server_result_are_logged(self) -> None:
        logger = RecordingLogger()
        strategy = BaselineStrategy("1001", StrategyConfig.default(), logger)
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=45, freshness=97),
            resources=[ResourceStock(station="S03", resource_type="ICE_BOX", amount=1, claim_frames=2)],
        )
        strategy.decide(state)
        self.assertTrue(any(event == "resource_intent" and fields.get("action") == "CLAIM_RESOURCE" and fields.get("resourceType") == "ICE_BOX" for event, fields in logger.records))

        accepted = GameState(
            frame=181,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=45, freshness=97, resources={"ICE_BOX": 1}),
            action_results=[{"playerId": "1001", "action": "CLAIM_RESOURCE", "accepted": True, "targetNodeId": "S03", "resourceType": "ICE_BOX"}],
        )
        strategy.decide(accepted)
        self.assertTrue(any(event == "resource_result" and fields.get("status") == "accepted" and fields.get("resourceType") == "ICE_BOX" for event, fields in logger.records))

    def test_squad_dispatch_and_server_result_are_logged(self) -> None:
        logger = RecordingLogger()
        strategy = BaselineStrategy("1001", StrategyConfig.default(), logger)
        moving = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.MOVING, station="S01", target="S02", task_score_base=90, squad_available=2),
            stations={"S02": Station(id="S02", has_obstacle=True)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        strategy.decide(moving)
        self.assertTrue(any(event == "squad_dispatch" and fields.get("action") == "SQUAD_CLEAR" and fields.get("target") == "S02" for event, fields in logger.records))

        accepted = GameState(
            frame=221,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.MOVING, station="S01", target="S02", task_score_base=90, squad_available=0),
            stations={"S02": Station(id="S02", has_obstacle=True)},
            edges=moving.edges,
            action_results=[{"playerId": "1001", "action": "SQUAD_CLEAR", "accepted": True, "targetNodeId": "S02"}],
        )
        strategy.decide(accepted)
        self.assertTrue(any(event == "squad_result" and fields.get("status") == "accepted" and fields.get("target") == "S02" for event, fields in logger.records))

    def test_reachable_ice_box_allows_quality_detour_at_target_score(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, freshness=94),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=10),
                RouteEdge(id="R1", start="S01", end="S02", distance=3),
                RouteEdge(id="R2", start="S02", end="S14", distance=10),
            ],
            resources=[ResourceStock(station="S02", resource_type="ICE_BOX", amount=1, claim_frames=2)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_early_route_prefers_nearby_ice_box_before_freshness_collapses(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=80,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=30, freshness=98),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=6),
                RouteEdge(id="R1", start="S01", end="S02", distance=1),
                RouteEdge(id="R2", start="S02", end="S03", distance=1),
                RouteEdge(id="R3", start="S03", end="S14", distance=6),
            ],
            resources=[ResourceStock(station="S03", resource_type="ICE_BOX", amount=1, claim_frames=2)],
            tasks=[TaskInstance(id="task-side", template="T08", target="S14", score=30, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_remote_low_value_task_does_not_wait_on_blocked_path(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", freshness=96),
            stations={"S02": Station(id="S02", has_obstacle=True)},
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=1),
                RouteEdge(id="T1", start="S01", end="S02", distance=1),
                RouteEdge(id="T2", start="S02", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="low-remote", template="T13", target="S02", score=15, process_frames=5)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_remote_low_value_task_skips_mandatory_live_guard_trap_wait(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=251,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=30, freshness=86),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S10", good_fruit=1),
            stations={"S09": Station(id="S09"), "S10": Station(id="S10", node_type="KEY_PASS"), "S12": Station(id="S12"), "S14": Station(id="S14")},
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", route_type="ROAD", distance=1),
                RouteEdge(id="E2", start="S10", end="S12", route_type="ROAD", distance=1),
                RouteEdge(id="E3", start="S12", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[TaskInstance(id="low-live-trap", template="T13", target="S12", score=15, process_frames=5)],
        )

        action = strategy.decide(state)

        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S10")

    def test_first_low_value_task_can_still_pull_convoy_through_live_guard_risk(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=0, freshness=94),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S10", good_fruit=1),
            stations={"S09": Station(id="S09"), "S10": Station(id="S10", node_type="KEY_PASS"), "S12": Station(id="S12"), "S14": Station(id="S14")},
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", route_type="ROAD", distance=1),
                RouteEdge(id="E2", start="S10", end="S12", route_type="ROAD", distance=1),
                RouteEdge(id="E3", start="S12", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="D1", start="S09", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[TaskInstance(id="first-low-live-trap", template="T13", target="S12", score=15, process_frames=5)],
        )

        task = strategy._best_reachable_task(state)

        self.assertIsNotNone(task)
        self.assertEqual(task.id, "first-low-live-trap")

    def test_second_low_value_task_skips_live_guard_risk_without_milestone(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=297,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S10", task_score_base=15, freshness=84),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S11", good_fruit=1),
            stations={"S10": Station(id="S10"), "S11": Station(id="S11"), "S12": Station(id="S12"), "S14": Station(id="S14")},
            edges=[
                RouteEdge(id="E1", start="S10", end="S11", route_type="ROAD", distance=1),
                RouteEdge(id="E2", start="S11", end="S12", route_type="ROAD", distance=1),
                RouteEdge(id="E3", start="S12", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="D1", start="S10", end="S14", route_type="ROAD", distance=2),
            ],
            tasks=[TaskInstance(id="second-low-live-trap", template="T13", target="S12", score=15, process_frames=5)],
        )

        action = strategy.decide(state)

        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_remote_thirty_point_task_can_still_pull_convoy_for_score_floor(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", freshness=96),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=1),
                RouteEdge(id="T1", start="S01", end="S02", distance=1),
                RouteEdge(id="T2", start="S02", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="score-floor", template="T08", target="S02", score=30, process_frames=5)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_reachable_task_allows_extra_detour_to_cross_task_milestone(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=45, freshness=96),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=1),
                RouteEdge(id="T1", start="S01", end="S02", distance=8),
                RouteEdge(id="T2", start="S02", end="S14", distance=12),
            ],
            tasks=[TaskInstance(id="milestone-60", template="T13", target="S02", score=15, process_frames=5)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_reachable_task_still_skips_same_detour_without_task_milestone(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=160,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=30, freshness=96),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=1),
                RouteEdge(id="T1", start="S01", end="S02", distance=8),
                RouteEdge(id="T2", start="S02", end="S14", distance=12),
            ],
            tasks=[TaskInstance(id="no-milestone", template="T13", target="S02", score=15, process_frames=5)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_delivery_deadline_skips_far_ice_box(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=540,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=120, freshness=90),
            edges=[
                RouteEdge(id="D1", start="S01", end="S14", distance=3),
                RouteEdge(id="D2", start="S14", end="S15", distance=1),
                RouteEdge(id="R1", start="S01", end="S02", distance=20),
                RouteEdge(id="R2", start="S02", end="S14", distance=20),
            ],
            resources=[ResourceStock(station="S02", resource_type="ICE_BOX", amount=1, claim_frames=2)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_hot_weather_uses_ice_box_before_quality_gap_opens(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=330,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=90,
                task_score_base=60,
                resources={"ICE_BOX": 1},
            ),
            weather=WeatherState(active_types=("HOT",)),
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_hot_forecast_uses_ice_box_before_quality_gap_opens(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=300,
            phase="NORMAL",
            player_id="1001",
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S03",
                freshness=90,
                task_score_base=90,
                resources={"ICE_BOX": 1},
            ),
            weather=WeatherState(forecast_types=("HOT",)),
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_low_freshness_after_target_score_skips_reachable_task(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=300,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", freshness=80, task_score_base=90),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=2),
                RouteEdge(id="T1", start="S01", end="S02", distance=1),
                RouteEdge(id="T2", start="S02", end="S14", distance=3),
            ],
            tasks=[TaskInstance(id="detour-task", template="T08", target="S02", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_squad_scouts_gate_when_delivery_score_is_ready(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S12", task_score_base=90, squad_available=4),
            stations={"S14": Station(id="S14", process_type="VERIFY", process_round=6)},
            edges=[
                RouteEdge(id="E1", start="S12", end="S13", distance=1),
                RouteEdge(id="E2", start="S13", end="S14", distance=1),
                RouteEdge(id="E3", start="S14", end="S15", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S13")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_SCOUT)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S14")

    def test_intel_marks_gate_before_verify_in_rush(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=500,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S14",
                verified=False,
                resources={"INTEL": 1},
            ),
            stations={"S14": Station(id="S14", process_type="VERIFY", process_round=6)},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "INTEL")
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_rejected_task_without_task_id_cools_down_last_attempt(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", task_score_base=30),
            edges=[RouteEdge(id="E1", start="S02", end="S14", distance=1)],
            tasks=[TaskInstance(id="task-lost", template="T01", target="S02", score=30, process_frames=3)],
        )
        self.assertEqual(strategy.decide(first).main.action, MainActionType.CLAIM_TASK)

        rejected = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", task_score_base=30),
            edges=[RouteEdge(id="E1", start="S02", end="S14", distance=1)],
            tasks=[TaskInstance(id="task-lost", template="T01", target="S02", score=30, process_frames=3)],
            action_results=[{"playerId": "1001", "action": "CLAIM_TASK", "accepted": False, "code": "NOT_AVAILABLE"}],
        )
        action = strategy.decide(rejected)
        self.assertNotEqual(action.main.action, MainActionType.CLAIM_TASK)

    def test_intel_scouts_valuable_route_target_when_squad_unavailable(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S01",
                task_score_base=90,
                resources={"INTEL": 1},
                squad_available=0,
            ),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="rich-task", template="T08", target="S03", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "INTEL")
        self.assertEqual(action.main.to_action()["targetNodeId"], "S03")

    def test_rejected_intel_target_cools_down_same_target(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S01",
                task_score_base=90,
                resources={"INTEL": 1},
                squad_available=0,
            ),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="rich-task", template="T08", target="S03", score=45, process_frames=4)],
        )
        self.assertEqual(strategy.decide(first).main.to_action()["resourceType"], "INTEL")

        rejected = GameState(
            frame=181,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S01",
                task_score_base=90,
                resources={"INTEL": 1},
                squad_available=0,
            ),
            edges=first.edges,
            tasks=first.tasks,
            action_results=[{"playerId": "1001", "action": "USE_RESOURCE", "accepted": False, "code": "TARGET_TOO_FAR"}],
        )
        action = strategy.decide(rejected)
        self.assertFalse(action.main.action == MainActionType.USE_RESOURCE and action.main.to_action().get("resourceType") == "INTEL")

    def test_intel_skips_target_beyond_raw_distance_limit(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=403,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S12", task_score_base=75, resources={"INTEL": 1}, squad_available=0),
            stations={"S13": Station(id="S13")},
            edges=[
                RouteEdge(id="FAR", start="S12", end="S13", distance=25),
                RouteEdge(id="GATE", start="S13", end="S14", distance=18),
            ],
            tasks=[TaskInstance(id="late-task", template="T13", target="S13", score=15, process_frames=5)],
        )
        action = strategy.decide(state)
        self.assertFalse(action.main.action == MainActionType.USE_RESOURCE and action.main.to_action().get("resourceType") == "INTEL")

    def test_intel_can_target_learned_guard_block(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=30),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        self.assertEqual(strategy.decide(first).main.action, MainActionType.MOVE)

        learned = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=30, resources={"INTEL": 1}, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=first.edges,
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S09"}],
        )
        action = strategy.decide(learned)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        payload = action.main.to_action()
        self.assertEqual(payload["resourceType"], "INTEL")
        self.assertEqual(payload["targetNodeId"], "S09")

    def test_intel_keeps_available_squad_for_valuable_scouting(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S01",
                task_score_base=90,
                resources={"INTEL": 1},
                squad_available=6,
            ),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="rich-task", template="T08", target="S03", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_SCOUT)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S03")

    def test_intel_is_saved_when_route_has_no_valuable_scout_target(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                status=ConvoyStatus.IDLE,
                station="S01",
                task_score_base=90,
                resources={"INTEL": 1},
                squad_available=0,
            ),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S14", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertNotEqual(action.main.to_action().get("resourceType"), "INTEL")

    def test_valuable_resource_allows_larger_detour(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, freshness=84),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=3),
                RouteEdge(id="R1", start="S01", end="S02", distance=4),
                RouteEdge(id="R2", start="S02", end="S14", distance=4),
            ],
            resources=[ResourceStock(station="S02", resource_type="ICE_BOX", amount=1, claim_frames=2)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_document_resource_is_claimed_only_when_already_at_station(self) -> None:
        strategy = self.make_strategy()
        at_station = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", task_score_base=120),
            resources=[ResourceStock(station="S03", resource_type="PASS_TOKEN", amount=1, claim_frames=2)],
        )
        claim = strategy.decide(at_station)
        self.assertEqual(claim.main.action, MainActionType.CLAIM_RESOURCE)
        self.assertEqual(claim.main.to_action()["resourceType"], "PASS_TOKEN")

        off_route = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=120),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=2),
                RouteEdge(id="R1", start="S01", end="S03", distance=1),
                RouteEdge(id="R2", start="S03", end="S14", distance=4),
            ],
            resources=[ResourceStock(station="S03", resource_type="PASS_TOKEN", amount=1, claim_frames=2)],
        )
        move = strategy.decide(off_route)
        self.assertEqual(move.main.action, MainActionType.MOVE)
        self.assertEqual(move.main.to_action()["targetNodeId"], "S14")

    def test_sets_zero_fruit_guard_on_non_key_opponent_next_hop(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S03", task_score_base=120, good_fruit=95),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S02", task_score_base=90),
            edges=[
                RouteEdge(id="O1", start="S02", end="S03", distance=1),
                RouteEdge(id="O2", start="S03", end="S14", distance=2),
                RouteEdge(id="M1", start="S03", end="S04", distance=1),
                RouteEdge(id="M2", start="S04", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S03": Station(id="S03")},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.SET_GUARD)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S03")
        self.assertEqual(action.main.to_action()["extraGoodFruit"], 0)

    def test_sets_heavy_guard_after_crossing_mandatory_chokepoint(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=320,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S10", task_score_base=95, good_fruit=98, freshness=95),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S08", task_score_base=90),
            edges=[
                RouteEdge(id="O1", start="S08", end="S09", distance=1),
                RouteEdge(id="O2", start="S09", end="S10", distance=1),
                RouteEdge(id="O3", start="S10", end="S11", distance=1),
                RouteEdge(id="O4", start="S11", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S10": Station(id="S10", node_type="KEY_PASS")},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.SET_GUARD)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S10")
        self.assertEqual(action.main.to_action()["extraGoodFruit"], 2)

    def test_does_not_guard_s09_before_crossing_s10_chokepoint(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=125, good_fruit=98, freshness=96),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S08", task_score_base=90),
            edges=[
                RouteEdge(id="O1", start="S08", end="S09", distance=1),
                RouteEdge(id="O2", start="S09", end="S10", distance=1),
                RouteEdge(id="O3", start="S10", end="S11", distance=1),
                RouteEdge(id="O4", start="S11", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S09": Station(id="S09", node_type="KEY_PASS")},
        )
        action = strategy.decide(state)
        self.assertNotEqual(action.main.action if action.main else None, MainActionType.SET_GUARD)

    def test_reinforces_existing_guard_at_mandatory_chokepoint(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=321,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S10", task_score_base=95, good_fruit=98, freshness=95, squad_available=2),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S08", task_score_base=90),
            edges=[
                RouteEdge(id="O1", start="S08", end="S09", distance=1),
                RouteEdge(id="O2", start="S09", end="S10", distance=1),
                RouteEdge(id="O3", start="S10", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S10": Station(id="S10", node_type="KEY_PASS", guard_owner="RED", guard_defense=4)},
        )
        action = strategy.decide(state)
        self.assertIsNone(action.main)
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_REINFORCE)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S10")

    def test_does_not_reinforce_own_guard_with_one_squad_member(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S03", task_score_base=120, good_fruit=95, squad_available=1),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S02", task_score_base=90),
            edges=[
                RouteEdge(id="O1", start="S02", end="S03", distance=1),
                RouteEdge(id="O2", start="S03", end="S14", distance=2),
                RouteEdge(id="M1", start="S03", end="S04", distance=1),
                RouteEdge(id="M2", start="S04", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S03": Station(id="S03", guard_owner="RED", guard_defense=2)},
        )
        action = strategy.decide(state)
        self.assertIsNone(action.squad)

    def test_sets_guard_when_opponent_is_moving_to_my_chokepoint(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=60, good_fruit=96, freshness=94),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.MOVING, station="S08", target="S09", task_score_base=90),
            edges=[
                RouteEdge(id="O1", start="S08", end="S09", distance=1),
                RouteEdge(id="M1", start="S09", end="S14", distance=2),
            ],
            stations={"S09": Station(id="S09")},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.SET_GUARD)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")
        self.assertEqual(action.main.to_action()["extraGoodFruit"], 2)

    def test_waits_when_all_next_hops_have_live_trap_risk(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, good_fruit=96, freshness=94),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S09", task_score_base=90, good_fruit=95),
            edges=[
                RouteEdge(id="RISK", start="S08", end="S09", distance=1),
                RouteEdge(id="RISK2", start="S09", end="S14", distance=1),
                RouteEdge(id="SAFE", start="S08", end="S10", distance=2),
                RouteEdge(id="SAFE2", start="S10", end="S14", distance=1),
            ],
            stations={"S09": Station(id="S09"), "S10": Station(id="S10")},
        )
        action = strategy.decide(state)
        self.assertIsNotNone(action.main)
        self.assertEqual(action.main.action, MainActionType.WAIT)

    def test_pushes_mandatory_chokepoint_instead_of_waiting_live_trap(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=249,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=90, good_fruit=96, freshness=94),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.MOVING, station="S09", target="S10", task_score_base=90, good_fruit=95),
            edges=[
                RouteEdge(id="PASS1", start="S09", end="S10", distance=1),
                RouteEdge(id="PASS2", start="S10", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S10": Station(id="S10")},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S10")

    def test_pushes_mandatory_delivery_next_hop_instead_of_stall_waiting(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=398,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S12", task_score_base=45, good_fruit=96, freshness=96),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S13", task_score_base=90, good_fruit=95),
            edges=[
                RouteEdge(id="PASS1", start="S12", end="S13", distance=1),
                RouteEdge(id="PASS2", start="S13", end="S14", distance=1),
            ],
            stations={"S13": Station(id="S13"), "S14": Station(id="S14")},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S13")

    def test_skips_reachable_task_when_target_is_live_trap(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=60, good_fruit=96, freshness=94),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S10", task_score_base=90, good_fruit=95),
            edges=[
                RouteEdge(id="TASK", start="S08", end="S10", distance=1),
                RouteEdge(id="SAFE", start="S08", end="S09", distance=2),
                RouteEdge(id="SAFE2", start="S09", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S10": Station(id="S10"), "S09": Station(id="S09")},
            tasks=[TaskInstance(id="trap-task", template="T02", target="S10", score=30, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_skips_task_when_opponent_is_racing_to_target(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=60, good_fruit=96, freshness=94),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.MOVING, station="S09", target="S10", task_score_base=90, good_fruit=95),
            edges=[
                RouteEdge(id="TASK", start="S08", end="S10", distance=1),
                RouteEdge(id="SAFE", start="S08", end="S09", distance=2),
                RouteEdge(id="SAFE2", start="S09", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S10": Station(id="S10"), "S09": Station(id="S09")},
            tasks=[TaskInstance(id="trap-task", template="T02", target="S10", score=30, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertIsNotNone(action.main)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_delivery_route_pushes_live_trap_when_no_alternate(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=176,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=0, good_fruit=96, freshness=90),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S09", task_score_base=75, good_fruit=95),
            edges=[
                RouteEdge(id="M1", start="S07", end="S09", distance=1),
                RouteEdge(id="M2", start="S09", end="S10", distance=1),
                RouteEdge(id="M3", start="S10", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S09": Station(id="S09"), "S10": Station(id="S10", node_type="KEY_PASS")},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_skips_chokepoint_task_when_opponent_can_arrive_first(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=60, good_fruit=96, freshness=94),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S09", task_score_base=90, good_fruit=95),
            edges=[
                RouteEdge(id="M1", start="S08", end="S10", distance=2),
                RouteEdge(id="O1", start="S09", end="S10", distance=1),
                RouteEdge(id="M2", start="S10", end="S14", distance=20),
                RouteEdge(id="D1", start="S08", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S10": Station(id="S10", node_type="KEY_PASS"), "S09": Station(id="S09")},
            tasks=[TaskInstance(id="trap-task", template="T02", target="S10", score=30, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S14")

    def test_guard_trap_does_not_preempt_reachable_task(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S03", task_score_base=120, good_fruit=95),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S02", task_score_base=90),
            edges=[
                RouteEdge(id="O1", start="S02", end="S03", distance=1),
                RouteEdge(id="O2", start="S03", end="S14", distance=4),
                RouteEdge(id="T1", start="S03", end="S05", distance=1),
                RouteEdge(id="T2", start="S05", end="S14", distance=2),
                RouteEdge(id="M1", start="S03", end="S04", distance=1),
                RouteEdge(id="M2", start="S04", end="S14", distance=1),
            ],
            stations={"S03": Station(id="S03")},
            tasks=[TaskInstance(id="rich-task", template="T08", target="S05", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S05")

    def test_guard_trap_skips_endgame(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=570,
            phase="NORMAL",
            max_frame=600,
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S03", task_score_base=120, good_fruit=95),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S02", task_score_base=90),
            edges=[
                RouteEdge(id="O1", start="S02", end="S03", distance=1),
                RouteEdge(id="O2", start="S03", end="S14", distance=2),
                RouteEdge(id="M1", start="S03", end="S04", distance=1),
                RouteEdge(id="M2", start="S04", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
            stations={"S03": Station(id="S03")},
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertNotEqual(action.main.action, MainActionType.SET_GUARD)

    def test_squad_clear_handles_obstacle_before_spending_good_fruit(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=60, squad_available=2, good_fruit=80),
            stations={"S02": Station(id="S02", has_obstacle=True)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertIsNone(action.main)
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_CLEAR)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S02")

        next_state = GameState(
            frame=201,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, squad_available=0, squad_in_flight=2, good_fruit=80),
            stations={"S02": Station(id="S02", has_obstacle=True)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        wait_action = strategy.decide(next_state)
        self.assertIsNotNone(wait_action.main)
        self.assertEqual(wait_action.main.action, MainActionType.WAIT)
        self.assertIsNone(wait_action.squad)

    def test_proactive_squad_clear_targets_future_critical_obstacle(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=60, squad_available=2, good_fruit=80),
            stations={"S04": Station(id="S04", has_obstacle=True)},
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S04", distance=1),
                RouteEdge(id="E4", start="S04", end="S14", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_CLEAR)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S04")

    def test_proactive_squad_clear_targets_second_hop_obstacle(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=60, squad_available=2, good_fruit=80),
            stations={"S03": Station(id="S03", has_obstacle=True)},
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
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_CLEAR)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S03")

    def test_station_task_can_pair_with_proactive_squad_clear(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=60, squad_available=2, good_fruit=80),
            stations={"S03": Station(id="S03", has_obstacle=True)},
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S14", distance=1),
            ],
            tasks=[TaskInstance(id="current-task", template="T01", target="S01", score=20, process_frames=2)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.main.to_action()["taskId"], "current-task")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_CLEAR)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S03")

    def test_rush_delivery_lock_allows_proactive_clear_on_mandatory_path(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=520,
            max_frame=600,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(
                player_id="1001",
                team_id="RED",
                status=ConvoyStatus.IDLE,
                station="S10",
                task_score_base=130,
                squad_available=2,
                good_fruit=95,
                freshness=100,
                rush_tactic_used_count=1,
            ),
            stations={"S12": Station(id="S12", has_obstacle=True)},
            edges=[
                RouteEdge(id="E1", start="S10", end="S11", distance=1),
                RouteEdge(id="E2", start="S11", end="S12", distance=1),
                RouteEdge(id="E3", start="S12", end="S14", distance=1),
                RouteEdge(id="E4", start="S14", end="S15", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S11")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_CLEAR)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S12")

    def test_proactive_squad_clear_skips_future_obstacle_when_alternate_is_cheap(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, squad_available=4, good_fruit=80),
            stations={"S04": Station(id="S04", has_obstacle=True)},
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S04", distance=1),
                RouteEdge(id="E4", start="S04", end="S14", distance=1),
                RouteEdge(id="A1", start="S03", end="S05", distance=1),
                RouteEdge(id="A2", start="S05", end="S14", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertNotEqual(action.squad.action if action.squad else None, SquadActionType.SQUAD_CLEAR)

    def test_proactive_squad_clear_continues_after_adjacent_t04_skip(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=60, squad_available=2, good_fruit=80),
            stations={"S03": Station(id="S03", has_obstacle=True), "S04": Station(id="S04", has_obstacle=True)},
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S03", distance=1),
                RouteEdge(id="E3", start="S03", end="S04", distance=1),
                RouteEdge(id="E4", start="S04", end="S14", distance=1),
                RouteEdge(id="ADJ", start="S01", end="S03", distance=100),
            ],
            tasks=[TaskInstance(id="clear-s03", template="T04", target="S03", score=30, process_frames=6)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)
        self.assertEqual(action.main.to_action()["taskId"], "clear-s03")
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_CLEAR)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S04")

    def test_key_obstacle_pairs_squad_clear_with_main_forced_pass_under_pressure(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=390,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S10", task_score_base=130, squad_available=2, good_fruit=80),
            stations={"S11": Station(id="S11", has_obstacle=True)},
            edges=[RouteEdge(id="E1", start="S10", end="S11", distance=1), RouteEdge(id="E2", start="S11", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S11")
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_CLEAR)
        self.assertEqual(action.squad.to_action()["targetNodeId"], "S11")

    def test_obstacle_forced_pass_saves_good_fruit_when_no_squad(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, squad_available=0, good_fruit=80),
            stations={"S02": Station(id="S02", has_obstacle=True)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_enemy_guard_does_not_use_squad_weaken(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, squad_available=1, good_fruit=80),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=2)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertIsNotNone(action.main)
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertNotEqual(action.squad.action if action.squad else None, SquadActionType.SQUAD_WEAKEN)

    def test_moving_learned_guard_without_public_guard_does_not_squad_weaken(self) -> None:
        strategy = self.make_strategy()
        learned = GameState(
            frame=208,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=4),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S09"}],
        )
        strategy.decide(learned)

        moving = GameState(
            frame=209,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.MOVING, station="S08", target="S09", task_score_base=95, squad_available=4),
            stations={"S09": Station(id="S09")},
            edges=learned.edges,
        )
        action = strategy.decide(moving)
        self.assertNotEqual(action.squad.action if action.squad else None, SquadActionType.SQUAD_WEAKEN)

    def test_heavy_enemy_guard_on_mandatory_chokepoint_uses_squad_before_fruit_combo(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=360,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=95, good_fruit=98, bad_fruit=1),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S01", task_score_base=90),
            stations={"S10": Station(id="S10", node_type="KEY_PASS", guard_owner="BLUE", guard_defense=5)},
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", distance=1),
                RouteEdge(id="E2", start="S10", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.BREAK_GUARD)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S10")
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_WEAKEN)
        payload = action.squad.to_action()
        self.assertEqual(payload["targetNodeId"], "S10")

    def test_action_bundle_keeps_main_and_squad_actions_in_separate_protocol_slots(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=360,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=95, good_fruit=98, bad_fruit=1),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S01", task_score_base=90),
            stations={"S10": Station(id="S10", node_type="KEY_PASS", guard_owner="BLUE", guard_defense=5)},
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", distance=1),
                RouteEdge(id="E2", start="S10", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
        )
        action = strategy.decide(state)
        actions = action.to_actions()
        self.assertEqual(actions[0]["action"], "BREAK_GUARD")
        self.assertEqual(actions[1]["action"], "SQUAD_WEAKEN")
        self.assertTrue(action.main.action in MainActionType)
        self.assertTrue(action.squad.action in SquadActionType)

    def test_heavy_enemy_guard_uses_fruit_combo_when_squad_unavailable(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=360,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=95, good_fruit=98, bad_fruit=1, squad_available=0),
            opponent=PlayerState(player_id="2002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S01", task_score_base=90),
            stations={"S10": Station(id="S10", node_type="KEY_PASS", guard_owner="BLUE", guard_defense=5)},
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", distance=1),
                RouteEdge(id="E2", start="S10", end="S14", distance=1),
                RouteEdge(id="T", start="S14", end="S15", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.BREAK_GUARD)
        payload = action.main.to_action()
        self.assertEqual(payload["targetNodeId"], "S10")
        self.assertEqual(payload["goodFruit"], 1)
        self.assertEqual(payload["badFruit"], 1)

    def test_enemy_guard_uses_bad_fruit_before_forced_pass(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, squad_available=0, good_fruit=80, bad_fruit=1),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=3)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.BREAK_GUARD)
        self.assertEqual(action.main.to_action()["badFruit"], 1)
        self.assertEqual(action.main.to_action()["goodFruit"], 0)

    def test_enemy_guard_forced_pass_saves_good_fruit_when_no_bad_or_squad(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, squad_available=0, good_fruit=99),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=4)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.FORCED_PASS)
        self.assertNotIn("goodFruit", action.main.to_action())

    def test_no_blocker_feedback_moves_instead_of_repeating_forced_pass(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=249,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, squad_available=0, good_fruit=90, rush_tactic_used_count=1),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=2)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
        )
        self.assertEqual(strategy.decide(first).main.action, MainActionType.FORCED_PASS)

        stale = GameState(
            frame=250,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, squad_available=0, good_fruit=90, rush_tactic_used_count=1),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=2)},
            edges=[RouteEdge(id="E1", start="S01", end="S02", distance=1), RouteEdge(id="E2", start="S02", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "FORCED_PASS", "accepted": False, "code": "NO_BLOCKER", "targetNodeId": "S01"}],
        )
        action = strategy.decide(stale)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_intel_falls_back_to_upcoming_chokepoint_when_no_high_value_target(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=240,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=95, resources={"INTEL": 1}, squad_available=0),
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", distance=1),
                RouteEdge(id="E2", start="S10", end="S11", distance=1),
                RouteEdge(id="E3", start="S11", end="S14", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        payload = action.main.to_action()
        self.assertEqual(payload["resourceType"], "INTEL")
        self.assertEqual(payload["targetNodeId"], "S10")

    def test_intel_skips_upcoming_chokepoint_outside_protocol_range(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=240,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=95, resources={"INTEL": 1}, squad_available=0),
            edges=[
                RouteEdge(id="E1", start="S07", end="S09", distance=8),
                RouteEdge(id="E2", start="S09", end="S10", distance=8),
                RouteEdge(id="E3", start="S10", end="S14", distance=1),
            ],
        )
        action = strategy.decide(state)
        payload = action.main.to_action()
        self.assertFalse(action.main.action == MainActionType.USE_RESOURCE and payload.get("resourceType") == "INTEL")

    def test_opponent_high_score_fast_route_triggers_race_pressure(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=80),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S13", task_score_base=180),
            edges=[
                RouteEdge(id="MY", start="S01", end="S14", distance=35),
                RouteEdge(id="OPP", start="S13", end="S14", distance=2),
            ],
        )
        self.assertTrue(strategy.opponent_race_pressure(state))

    def test_opponent_race_pressure_chases_high_score_task_before_low_score_delivery(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=80, squad_available=0),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.IDLE, station="S13", task_score_base=180),
            edges=[
                RouteEdge(id="E1", start="S01", end="S02", distance=1),
                RouteEdge(id="E2", start="S02", end="S14", distance=35),
                RouteEdge(id="E3", start="S01", end="S14", distance=30),
                RouteEdge(id="E4", start="S13", end="S14", distance=2),
            ],
            tasks=[TaskInstance(id="catchup-45", template="T08", target="S02", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_delivered_opponent_terminal_guard_preserves_squad_and_weakens_key_point(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=420,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=100, squad_available=4, good_fruit=98),
            opponent=PlayerState(player_id="1002", team_id="BLUE", status=ConvoyStatus.DELIVERED, station="S15", task_score_base=180, delivered=True, verified=True),
            stations={"S10": Station(id="S10", guard_owner="BLUE", guard_defense=5)},
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", distance=1),
                RouteEdge(id="E2", start="S10", end="S14", distance=1),
                RouteEdge(id="E3", start="S14", end="S15", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertIsNotNone(action.squad)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_WEAKEN)
        self.assertEqual(action.squad.target, "S10")
        self.assertIn(action.main.action, {MainActionType.BREAK_GUARD, MainActionType.FORCED_PASS})

    def test_move_blocked_event_without_target_uses_last_attempted_move_and_avoids_repeat_move(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=318,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S10": Station(id="S10"), "S09": Station(id="S09")},
            edges=[
                RouteEdge(id="BAD", start="S08", end="S10", distance=1),
                RouteEdge(id="BAD2", start="S10", end="S14", distance=1),
                RouteEdge(id="ALT", start="S08", end="S09", distance=2),
                RouteEdge(id="ALT2", start="S09", end="S14", distance=1),
            ],
        )
        self.assertEqual(strategy.decide(first).main.to_action()["targetNodeId"], "S10")

        blocked = GameState(
            frame=319,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations=first.stations,
            edges=first.edges,
            events=[{"type": "MOVE_BLOCKED_BY_GUARD", "playerId": "1001"}],
        )
        action = strategy.decide(blocked)
        self.assertFalse(action.main.action == MainActionType.MOVE and action.main.to_action().get("targetNodeId") == "S10")
        self.assertEqual(action.main.to_action().get("targetNodeId"), "S09")

    def test_move_blocked_result_node_field_binds_guard_target_and_avoids_repeat_move(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=315,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S10": Station(id="S10"), "S09": Station(id="S09")},
            edges=[
                RouteEdge(id="BAD", start="S08", end="S10", distance=1),
                RouteEdge(id="BAD2", start="S10", end="S14", distance=1),
                RouteEdge(id="ALT", start="S08", end="S09", distance=2),
                RouteEdge(id="ALT2", start="S09", end="S14", distance=1),
            ],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "node": "S10"}],
        )
        action = strategy.decide(state)
        self.assertFalse(action.main.action == MainActionType.MOVE and action.main.to_action().get("targetNodeId") == "S10")
        self.assertEqual(action.main.to_action().get("targetNodeId"), "S09")

    def test_move_blocked_node_field_stops_plain_move_when_no_alternate(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=319,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S10": Station(id="S10")},
            edges=[
                RouteEdge(id="BAD", start="S08", end="S10", distance=1),
                RouteEdge(id="BAD2", start="S10", end="S14", distance=1),
            ],
            action_results=[
                {"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "node": "S10"},
                {"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "node": "S10"},
                {"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "node": "S10"},
            ],
        )
        action = strategy.decide(state)
        self.assertNotEqual(action.main.action, MainActionType.MOVE)
        self.assertIn(action.main.action, {MainActionType.FORCED_PASS, MainActionType.BREAK_GUARD, MainActionType.WAIT})

    def test_delivery_lock_no_plain_move_after_s14_blocked(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=370,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S13", task_score_base=120, squad_available=0),
            stations={"S14": Station(id="S14")},
            edges=[RouteEdge(id="GATE", start="S13", end="S14", distance=1), RouteEdge(id="TERM", start="S14", end="S15", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S14"}],
        )
        action = strategy.decide(state)
        self.assertFalse(action.main.action == MainActionType.MOVE and action.main.to_action().get("targetNodeId") == "S14")
        self.assertIn(action.main.action, {MainActionType.FORCED_PASS, MainActionType.BREAK_GUARD, MainActionType.WAIT})

    def test_delivery_blocker_uses_forced_pass_when_squad_weaken_invalid(self) -> None:
        strategy = self.make_strategy()
        strategy._blocked_guard_nodes["S14"] = 1
        strategy._blocked_guard_last_frame["S14"] = 370
        strategy._guard_blocked_until["S14"] = 410
        strategy._delivery_blocker_nodes.add("S14")
        state = GameState(
            frame=371,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S13", task_score_base=120, squad_available=8),
            stations={"S14": Station(id="S14", guard_owner="BLUE", guard_defense=4)},
            edges=[RouteEdge(id="GATE", start="S13", end="S14", distance=1), RouteEdge(id="TERM", start="S14", end="S15", distance=1)],
            action_results=[{"playerId": "1001", "action": "SQUAD_WEAKEN", "accepted": False, "code": "INVALID_ACTION_TYPE", "nodeId": "S14"}],
        )
        action = strategy.decide(state)
        self.assertIn(action.main.action, {MainActionType.FORCED_PASS, MainActionType.BREAK_GUARD})
        self.assertFalse(action.main.action == MainActionType.MOVE and action.main.to_action().get("targetNodeId") == "S14")
        self.assertFalse(action.squad is not None and action.squad.action == SquadActionType.SQUAD_WEAKEN)

    def test_delivery_waits_for_active_squad_weaken(self) -> None:
        strategy = self.make_strategy()
        strategy._blocked_guard_nodes["S14"] = 1
        strategy._blocked_guard_last_frame["S14"] = 370
        strategy._guard_blocked_until["S14"] = 410
        strategy._delivery_blocker_nodes.add("S14")
        strategy._squad_weaken_until["S14"] = 380
        state = GameState(
            frame=371,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S13", task_score_base=120, squad_available=8),
            stations={"S14": Station(id="S14", guard_owner="BLUE", guard_defense=4)},
            edges=[RouteEdge(id="GATE", start="S13", end="S14", distance=1), RouteEdge(id="TERM", start="S14", end="S15", distance=1)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.WAIT)
        self.assertIsNone(action.squad)

    def test_move_blocked_clears_stale_no_blocker(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=370,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S13", task_score_base=120),
            edges=[RouteEdge(id="GATE", start="S13", end="S14", distance=1), RouteEdge(id="TERM", start="S14", end="S15", distance=1)],
        )
        strategy._no_blocker_until["S14"] = 390
        strategy._learn_error_code(state, "MOVE", "MOVE_BLOCKED_BY_GUARD", "S14", None, None, {})
        self.assertNotIn("S14", strategy._no_blocker_until)
        self.assertGreater(strategy._guard_blocked_until.get("S14", -1), state.frame)
        self.assertIn("S14", strategy._delivery_blocker_nodes)

    def test_final_guard_intercepts_plain_move_to_blocked_gate(self) -> None:
        strategy = self.make_strategy()
        strategy._blocked_guard_nodes["S14"] = 1
        strategy._blocked_guard_last_frame["S14"] = 370
        strategy._guard_blocked_until["S14"] = 410
        strategy._delivery_blocker_nodes.add("S14")
        state = GameState(
            frame=371,
            phase="RUSH",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S13", task_score_base=120, squad_available=0),
            stations={"S14": Station(id="S14")},
            edges=[RouteEdge(id="GATE", start="S13", end="S14", distance=1), RouteEdge(id="TERM", start="S14", end="S15", distance=1)],
        )
        guarded = strategy._final_guard_delivery_blocker(state, ActionBundle(main=MainAction(MainActionType.MOVE, target="S14")))
        self.assertFalse(guarded.main.action == MainActionType.MOVE and guarded.main.to_action().get("targetNodeId") == "S14")
        self.assertIn(guarded.main.action, {MainActionType.FORCED_PASS, MainActionType.BREAK_GUARD, MainActionType.WAIT})

    def test_learned_guard_ttl_expires_and_allows_probe_move(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S09": Station(id="S09")},
            edges=[RouteEdge(id="E1", start="S08", end="S09", distance=1), RouteEdge(id="E2", start="S09", end="S14", distance=1)],
        )
        self.assertEqual(strategy.decide(first).main.to_action()["targetNodeId"], "S09")
        blocked = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations=first.stations,
            edges=first.edges,
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD"}],
        )
        strategy.decide(blocked)
        expired = GameState(
            frame=195,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations=first.stations,
            edges=first.edges,
        )
        action = strategy.decide(expired)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_key_guard_memory_keeps_longer_ttl_than_regular_node(self) -> None:
        strategy = self.make_strategy()
        first = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations={"S10": Station(id="S10", node_type="KEY_PASS"), "S09": Station(id="S09")},
            edges=[
                RouteEdge(id="DIRECT1", start="S08", end="S10", distance=1),
                RouteEdge(id="DIRECT2", start="S10", end="S14", distance=1),
                RouteEdge(id="ALT1", start="S08", end="S09", distance=2),
                RouteEdge(id="ALT2", start="S09", end="S14", distance=1),
            ],
        )
        self.assertEqual(strategy.decide(first).main.to_action()["targetNodeId"], "S10")

        blocked = GameState(
            frame=101,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations=first.stations,
            edges=first.edges,
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD"}],
        )
        strategy.decide(blocked)

        still_remembered = GameState(
            frame=170,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=95, squad_available=0),
            stations=first.stations,
            edges=first.edges,
        )
        action = strategy.decide(still_remembered)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_squad_weaken_in_flight_waits_before_retrying_same_target(self) -> None:
        strategy = self.make_strategy()
        guarded = GameState(
            frame=330,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=95, squad_available=4, good_fruit=98, bad_fruit=1),
            stations={"S10": Station(id="S10", node_type="KEY_PASS", guard_owner="BLUE", guard_defense=5)},
            edges=[RouteEdge(id="E1", start="S09", end="S10", distance=1), RouteEdge(id="E2", start="S10", end="S14", distance=1)],
        )
        first = strategy.decide(guarded)
        self.assertEqual(first.squad.action, SquadActionType.SQUAD_WEAKEN)

        waiting = GameState(
            frame=331,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S09", task_score_base=95, squad_available=2, good_fruit=98),
            stations={"S10": Station(id="S10", node_type="KEY_PASS", guard_owner="BLUE", guard_defense=5)},
            edges=guarded.edges,
        )
        retry = strategy.decide(waiting)
        self.assertFalse(retry.main and retry.main.action == MainActionType.MOVE and retry.main.to_action().get("targetNodeId") == "S10")

    def test_route_package_prefers_higher_task_score_road_package(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=0, squad_available=0),
            edges=[
                RouteEdge(id="M1", start="S01", end="S08", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S08", end="S14", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="R1", start="S01", end="S02", route_type="ROAD", distance=2),
                RouteEdge(id="R2", start="S02", end="S03", route_type="ROAD", distance=1),
                RouteEdge(id="R3", start="S03", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[
                TaskInstance(id="road-45", template="T08", target="S02", score=45, process_frames=4),
                TaskInstance(id="road-60", template="T08", target="S03", score=60, process_frames=4),
                TaskInstance(id="mountain-30", template="T08", target="S08", score=30, process_frames=4),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_route_package_cost_includes_task_process_frames(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=0, squad_available=0),
            edges=[
                RouteEdge(id="M1", start="S01", end="S08", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S08", end="S14", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="R1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="R2", start="S02", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[TaskInstance(id="slow-road", template="T08", target="S02", score=90, process_frames=80)],
        )
        packages = strategy._route_packages_to_gate(state)
        road = next(package for package in packages if package["path"] == ("S01", "S02", "S14"))
        mountain = next(package for package in packages if package["path"] == ("S01", "S08", "S14"))
        self.assertGreaterEqual(road["cost"] - mountain["cost"], 75)

    def test_station_task_skips_expired_task(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=100,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S03", task_score_base=30),
            edges=[RouteEdge(id="G", start="S03", end="S14", route_type="ROAD", distance=1)],
            tasks=[TaskInstance(id="expired", template="T08", target="S03", score=45, process_frames=4, expire_frame=99)],
        )
        action = strategy.decide(state)
        self.assertNotEqual(action.main.action, MainActionType.CLAIM_TASK)

    def test_route_package_values_delivery_score_jump_to_ninety_tasks(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=0, squad_available=0),
            edges=[
                RouteEdge(id="LOW1", start="S01", end="S08", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="LOW2", start="S08", end="S14", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="HIGH1", start="S01", end="S02", route_type="ROAD", distance=2),
                RouteEdge(id="HIGH2", start="S02", end="S03", route_type="ROAD", distance=1),
                RouteEdge(id="HIGH3", start="S03", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[
                TaskInstance(id="low-60", template="T08", target="S08", score=60, process_frames=4),
                TaskInstance(id="high-45-a", template="T08", target="S02", score=45, process_frames=4),
                TaskInstance(id="high-45-b", template="T08", target="S03", score=45, process_frames=4),
            ],
        )
        packages = strategy._route_packages_to_gate(state)
        low = next(package for package in packages if package["path"] == ("S01", "S08", "S14"))
        high = next(package for package in packages if package["path"] == ("S01", "S02", "S03", "S14"))
        self.assertEqual(high["task_score"] - low["task_score"], 30)
        self.assertGreaterEqual(high["score_value"] - low["score_value"], 70)

        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_route_package_score_value_includes_time_task_coefficient(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=0),
            edges=[RouteEdge(id="GT", start="S14", end="S15", route_type="ROAD", distance=1)],
        )

        score_75 = strategy._route_package_score_value(state, 75, 10)
        score_90 = strategy._route_package_score_value(state, 90, 10)
        fast_90 = strategy._route_package_score_value(state, 90, 10)
        slow_90 = strategy._route_package_score_value(state, 90, 100)

        self.assertGreater(score_90 - score_75, 55)
        self.assertGreater(fast_90, slow_90)

    def test_route_package_allows_large_detour_when_delivery_score_value_gap_is_large(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=171,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=30, freshness=91, squad_available=0),
            edges=[
                RouteEdge(id="D1", start="S07", end="S09", route_type="ROAD", distance=1),
                RouteEdge(id="D2", start="S09", end="S10", route_type="ROAD", distance=1),
                RouteEdge(id="D3", start="S10", end="S11", route_type="ROAD", distance=1),
                RouteEdge(id="D4", start="S11", end="S12", route_type="ROAD", distance=1),
                RouteEdge(id="D5", start="S12", end="S13", route_type="ROAD", distance=1),
                RouteEdge(id="D6", start="S13", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="H1", start="S07", end="S08", route_type="ROAD", distance=45),
                RouteEdge(id="H2", start="S08", end="S10", route_type="ROAD", distance=1),
            ],
            tasks=[
                TaskInstance(id="high-45", template="T08", target="S08", score=45, process_frames=4),
                TaskInstance(id="high-30", template="T11", target="S08", score=30, process_frames=4),
            ],
        )
        packages = strategy._route_packages_to_gate(state)
        direct = next(package for package in packages if package["path"] == ("S07", "S09", "S10", "S11", "S12", "S13", "S14"))
        high = next(package for package in packages if package["path"] == ("S07", "S08", "S10", "S11", "S12", "S13", "S14"))
        self.assertGreaterEqual(high["score_value"] - direct["score_value"], 150)
        self.assertGreater(high["cost"] - direct["cost"], 36)

        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S08")

    def test_route_package_score_value_detour_tightens_under_freshness_pressure(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=171,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=30, freshness=76, squad_available=0),
            edges=[
                RouteEdge(id="D1", start="S07", end="S09", route_type="ROAD", distance=1),
                RouteEdge(id="D2", start="S09", end="S10", route_type="ROAD", distance=1),
                RouteEdge(id="D3", start="S10", end="S11", route_type="ROAD", distance=1),
                RouteEdge(id="D4", start="S11", end="S12", route_type="ROAD", distance=1),
                RouteEdge(id="D5", start="S12", end="S13", route_type="ROAD", distance=1),
                RouteEdge(id="D6", start="S13", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="H1", start="S07", end="S08", route_type="ROAD", distance=45),
                RouteEdge(id="H2", start="S08", end="S10", route_type="ROAD", distance=1),
            ],
            tasks=[
                TaskInstance(id="high-45", template="T08", target="S08", score=45, process_frames=4),
                TaskInstance(id="high-30", template="T11", target="S08", score=30, process_frames=4),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_route_package_allows_safe_road_package_to_reach_ninety_task_score(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=30, freshness=92, squad_available=0),
            edges=[
                RouteEdge(id="M1", start="S07", end="S09", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S09", end="S14", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="R1", start="S07", end="S08", route_type="ROAD", distance=38),
                RouteEdge(id="R2", start="S08", end="S10", route_type="ROAD", distance=1),
                RouteEdge(id="R3", start="S10", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[
                TaskInstance(id="road-30-a", template="T08", target="S08", route_bucket="ROAD", score=30, process_frames=4),
                TaskInstance(id="road-30-b", template="T11", target="S10", route_bucket="ROAD", score=30, process_frames=4),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S08")

    def test_task_requirement_fields_skip_unclaimable_task(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=190,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=30, resources={}),
            edges=[RouteEdge(id="E1", start="S07", end="S14", distance=1)],
            tasks=[
                TaskInstance(
                    id="needs-permit",
                    template="T09",
                    target="S07",
                    score=30,
                    process_frames=4,
                    raw={"requiredResourceTypes": ["OFFICIAL_PERMIT"]},
                )
            ],
        )
        action = strategy.decide(state)
        self.assertNotEqual(action.main.action, MainActionType.CLAIM_TASK)

    def test_route_package_large_score_value_detour_requires_deadline_buffer(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=500,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S07", task_score_base=30, freshness=91, squad_available=0),
            edges=[
                RouteEdge(id="D1", start="S07", end="S09", route_type="ROAD", distance=1),
                RouteEdge(id="D2", start="S09", end="S10", route_type="ROAD", distance=1),
                RouteEdge(id="D3", start="S10", end="S11", route_type="ROAD", distance=1),
                RouteEdge(id="D4", start="S11", end="S12", route_type="ROAD", distance=1),
                RouteEdge(id="D5", start="S12", end="S13", route_type="ROAD", distance=1),
                RouteEdge(id="D6", start="S13", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="H1", start="S07", end="S08", route_type="ROAD", distance=45),
                RouteEdge(id="H2", start="S08", end="S10", route_type="ROAD", distance=1),
            ],
            tasks=[
                TaskInstance(id="high-45", template="T08", target="S08", score=45, process_frames=4),
                TaskInstance(id="high-30", template="T11", target="S08", score=30, process_frames=4),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S09")

    def test_route_package_penalizes_key_learned_guard_without_squad(self) -> None:
        strategy = self.make_strategy()
        blocked = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", squad_available=0),
            stations={"S10": Station(id="S10", node_type="KEY_PASS")},
            edges=[
                RouteEdge(id="DIRECT1", start="S01", end="S10", route_type="ROAD", distance=1),
                RouteEdge(id="DIRECT2", start="S10", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="ALT1", start="S01", end="S08", route_type="MOUNTAIN", distance=2),
                RouteEdge(id="ALT2", start="S08", end="S14", route_type="MOUNTAIN", distance=2),
            ],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S10"}],
        )
        strategy.decide(blocked)
        packages = strategy._route_packages_to_gate(blocked)
        direct = next(package for package in packages if package["path"] == ("S01", "S10", "S14"))
        alternate = next(package for package in packages if package["path"] == ("S01", "S08", "S14"))
        self.assertGreaterEqual(direct["guard_resolution_cost"], 40)
        self.assertLess(direct["adjusted_score_value"], alternate["adjusted_score_value"])

    def test_route_package_uses_safety_gate_for_guarded_next_hop(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=0, squad_available=0),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=2)},
            edges=[
                RouteEdge(id="M1", start="S01", end="S08", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S08", end="S14", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="R1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="R2", start="S02", end="S03", route_type="ROAD", distance=1),
                RouteEdge(id="R3", start="S03", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[
                TaskInstance(id="road-60", template="T08", target="S02", score=60, process_frames=4),
                TaskInstance(id="road-45", template="T08", target="S03", score=45, process_frames=4),
            ],
        )
        action = strategy.decide(state)
        self.assertNotEqual(action.main.action, MainActionType.MOVE)
        self.assertIn(action.main.action, {MainActionType.FORCED_PASS, MainActionType.BREAK_GUARD})
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_route_package_next_hop_keeps_gate_objective_for_learned_guard_alternate(self) -> None:
        strategy = self.make_strategy()
        learned = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=0, squad_available=0),
            edges=[
                RouteEdge(id="HIGH1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="HIGH2", start="S02", end="S04", route_type="ROAD", distance=1),
                RouteEdge(id="HIGH3", start="S04", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="ALT1", start="S01", end="S03", route_type="MOUNTAIN", distance=2),
                RouteEdge(id="ALT2", start="S03", end="S14", route_type="MOUNTAIN", distance=1),
            ],
            tasks=[
                TaskInstance(id="road-60", template="T08", target="S02", score=60, process_frames=4),
                TaskInstance(id="road-45", template="T08", target="S04", score=45, process_frames=4),
            ],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S02"}],
        )
        action = strategy.decide(learned)
        self.assertIn(action.main.action, {MainActionType.FORCED_PASS, MainActionType.BREAK_GUARD})
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_route_package_allows_mountain_escape_after_repeated_mainline_block(self) -> None:
        strategy = self.make_strategy()
        learned = GameState(
            frame=260,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=95, squad_available=0),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=8, raw={"guard": {"ownerTeamId": "BLUE", "defense": 8, "initialDefense": 8, "ageRound": 0, "active": True}})},
            edges=[
                RouteEdge(id="HIGH1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="HIGH2", start="S02", end="S04", route_type="ROAD", distance=1),
                RouteEdge(id="HIGH3", start="S04", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="ALT1", start="S01", end="S03", route_type="MOUNTAIN", distance=2),
                RouteEdge(id="ALT2", start="S03", end="S14", route_type="MOUNTAIN", distance=1),
            ],
            tasks=[
                TaskInstance(id="road-30", template="T08", target="S04", score=30, process_frames=4),
            ],
            action_results=[
                {"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S02"},
                {"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S02"},
            ],
        )
        action = strategy.decide(learned)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S03")

    def test_mountain_without_skipped_task_not_penalized(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=30, squad_available=0),
            edges=[
                RouteEdge(id="M1", start="S01", end="S08", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S08", end="S14", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="R1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="R2", start="S02", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[TaskInstance(id="both-30", template="T08", target="S08", score=30, process_frames=4)],
        )
        mountain = next(package for package in strategy._route_packages_to_gate(state) if package["path"] == ("S01", "S08", "S14"))
        self.assertTrue(mountain["mountain_route"])
        self.assertFalse(mountain["escape_route"])
        self.assertEqual(mountain["escape_reason"], "no_skipped_task")
        self.assertEqual(mountain["missed_task_penalty"], 0)

    def test_guard_decay_remaining_key_pass(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", station="S01"),
            stations={"S10": Station(id="S10", node_type="KEY_PASS", guard_owner="BLUE", guard_defense=4, raw={"guard": {"ownerTeamId": "BLUE", "defense": 4, "initialDefense": 4, "ageRound": 42, "active": True}})},
        )
        self.assertEqual(strategy._guard_decay_remaining_frames(state, "S10"), 113)

    def test_guard_decay_remaining_gate_soon(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", station="S13"),
            stations={"S14": Station(id="S14", guard_owner="BLUE", guard_defense=1, raw={"guard": {"ownerTeamId": "BLUE", "defense": 1, "initialDefense": 1, "ageRound": 29, "active": True}})},
        )
        self.assertEqual(strategy._guard_decay_remaining_frames(state, "S14"), 1)

    def test_forced_pass_tax_gate(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", station="S13"),
            stations={"S14": Station(id="S14", guard_owner="BLUE", guard_defense=4, raw={"guard": {"ownerTeamId": "BLUE", "defense": 4, "initialDefense": 4, "ageRound": 0, "active": True}})},
        )
        self.assertEqual(strategy._forced_pass_tax_for_guard(state, "S14"), 32)

    def test_mainline_preferred_when_guard_decays_soon(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=30, squad_available=0),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=1, raw={"guard": {"ownerTeamId": "BLUE", "defense": 1, "initialDefense": 1, "ageRound": 29, "active": True}})},
            edges=[
                RouteEdge(id="R1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="R2", start="S02", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="M1", start="S01", end="S03", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S03", end="S14", route_type="MOUNTAIN", distance=1),
            ],
            tasks=[TaskInstance(id="road-45", template="T08", target="S02", score=45, process_frames=4)],
        )
        action = strategy.decide(state)
        self.assertIn(action.main.action, {MainActionType.FORCED_PASS, MainActionType.BREAK_GUARD})
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_escape_allowed_when_mainline_guard_cost_too_high(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=220,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=95, squad_available=0, good_fruit=20, bad_fruit=0),
            stations={"S02": Station(id="S02", node_type="KEY_PASS", guard_owner="BLUE", guard_defense=8, raw={"guard": {"ownerTeamId": "BLUE", "defense": 8, "initialDefense": 8, "ageRound": 0, "active": True}})},
            edges=[
                RouteEdge(id="R1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="R2", start="S02", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="M1", start="S01", end="S03", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S03", end="S14", route_type="MOUNTAIN", distance=1),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.MOVE)
        self.assertEqual(action.main.to_action()["targetNodeId"], "S03")

    def test_mainline_preferred_when_escape_skips_large_task_score(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=30, squad_available=0),
            stations={"S02": Station(id="S02", guard_owner="BLUE", guard_defense=1, raw={"guard": {"ownerTeamId": "BLUE", "defense": 1, "initialDefense": 1, "ageRound": 29, "active": True}})},
            edges=[
                RouteEdge(id="R1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="R2", start="S02", end="S04", route_type="ROAD", distance=1),
                RouteEdge(id="R3", start="S04", end="S14", route_type="ROAD", distance=1),
                RouteEdge(id="M1", start="S01", end="S03", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S03", end="S14", route_type="MOUNTAIN", distance=1),
            ],
            tasks=[
                TaskInstance(id="road-60", template="T08", target="S02", score=60, process_frames=4),
                TaskInstance(id="road-45", template="T08", target="S04", score=45, process_frames=4),
            ],
        )
        action = strategy.decide(state)
        self.assertIn(action.main.action, {MainActionType.FORCED_PASS, MainActionType.BREAK_GUARD})
        self.assertEqual(action.main.to_action()["targetNodeId"], "S02")

    def test_missing_guard_raw_falls_back_to_existing_behavior(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", station="S01", squad_available=0),
            stations={"S10": Station(id="S10", node_type="KEY_PASS", guard_owner="BLUE", guard_defense=4)},
        )
        resolution = strategy._guard_resolution_cost(state, "S10", "S14")
        self.assertEqual(resolution["mode"], "static_fallback")
        self.assertGreaterEqual(resolution["cost"], 60)
        self.assertIsNone(resolution["forcedPassTax"])
        self.assertIsNone(resolution["decayRemaining"])

    def test_mountain_package_records_missed_mainline_task_penalty(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=120,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=30, squad_available=0),
            edges=[
                RouteEdge(id="M1", start="S01", end="S08", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="M2", start="S08", end="S14", route_type="MOUNTAIN", distance=1),
                RouteEdge(id="R1", start="S01", end="S02", route_type="ROAD", distance=1),
                RouteEdge(id="R2", start="S02", end="S03", route_type="ROAD", distance=1),
                RouteEdge(id="R3", start="S03", end="S14", route_type="ROAD", distance=1),
            ],
            tasks=[
                TaskInstance(id="road-45", template="T08", target="S02", score=45, process_frames=4),
                TaskInstance(id="road-30", template="T08", target="S03", score=30, process_frames=4),
            ],
        )
        packages = strategy._route_packages_to_gate(state)
        mountain = next(package for package in packages if package["path"] == ("S01", "S08", "S14"))
        self.assertTrue(mountain["escape_route"])
        self.assertGreaterEqual(mountain["skipped_task_score"], 45)
        self.assertGreater(mountain["missed_task_penalty"], 0)
        self.assertLess(mountain["adjusted_score_value"], mountain["score_value"])

    def test_claim_task_object_busy_only_short_cools_without_global_reject(self) -> None:
        strategy = self.make_strategy()
        busy = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            tasks=[TaskInstance(id="task-busy", template="T08", target="S02", score=45, process_frames=4)],
            action_results=[{"playerId": "1001", "action": "CLAIM_TASK", "accepted": False, "code": "OBJECT_BUSY", "taskId": "task-busy"}],
        )
        strategy.decide(busy)
        retry = GameState(
            frame=206,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02"),
            tasks=busy.tasks,
        )
        action = strategy.decide(retry)
        self.assertEqual(action.main.action, MainActionType.CLAIM_TASK)

    def test_claim_resource_object_busy_only_short_cools_without_global_reject(self) -> None:
        strategy = self.make_strategy()
        busy = GameState(
            frame=200,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", freshness=90),
            resources=[ResourceStock(station="S03", resource_type="ICE_BOX", amount=1)],
            edges=[RouteEdge(id="E1", start="S03", end="S14", distance=4)],
            action_results=[{"playerId": "1001", "action": "CLAIM_RESOURCE", "accepted": False, "code": "OBJECT_BUSY", "targetNodeId": "S03", "resourceType": "ICE_BOX"}],
        )
        strategy.decide(busy)
        retry = GameState(
            frame=206,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S03", freshness=90),
            resources=busy.resources,
            edges=busy.edges,
        )
        self.assertNotIn(("S03", "ICE_BOX"), strategy._rejected_resource_keys)
        resource = strategy._best_urgent_station_resource(retry)
        self.assertIsNotNone(resource)
        self.assertEqual(resource.resource_type, "ICE_BOX")

    def test_projected_delivery_freshness_uses_ice_box_before_drop(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=389,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S09", task_score_base=90, freshness=82, resources={"ICE_BOX": 1}),
            edges=[
                RouteEdge(id="E1", start="S09", end="S10", route_type="MOUNTAIN", distance=12),
                RouteEdge(id="E2", start="S10", end="S14", route_type="MOUNTAIN", distance=12),
                RouteEdge(id="E3", start="S14", end="S15", distance=2),
            ],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_intel_prioritizes_blocked_guard_target_over_generic_scout(self) -> None:
        strategy = self.make_strategy()
        learned = GameState(
            frame=319,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S08", task_score_base=90, resources={"INTEL": 1}, squad_available=0),
            edges=[RouteEdge(id="E1", start="S08", end="S10", distance=1), RouteEdge(id="E2", start="S10", end="S14", distance=1)],
            action_results=[{"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S10"}],
        )
        action = strategy.decide(learned)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "INTEL")
        self.assertEqual(action.main.to_action()["targetNodeId"], "S10")

    def test_high_score_uses_intel_before_route_critical_fixed_process(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=360,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S11", task_score_base=120, resources={"INTEL": 1}),
            stations={"S11": Station(id="S11", process_type="PASS_TRANSFER", process_round=5)},
            edges=[RouteEdge(id="E1", start="S11", end="S13", distance=1), RouteEdge(id="E2", start="S13", end="S14", distance=1)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "INTEL")
        self.assertEqual(action.main.to_action()["targetNodeId"], "S11")

    def test_low_score_early_process_does_not_spend_intel(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=40,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S02", task_score_base=0, resources={"INTEL": 1}),
            stations={"S02": Station(id="S02", process_type="TRANSFER", process_round=4)},
            edges=[RouteEdge(id="E1", start="S02", end="S03", distance=1)],
        )
        action = strategy.decide(state)
        self.assertEqual(action.main.action, MainActionType.PROCESS)


if __name__ == "__main__":
    unittest.main()
