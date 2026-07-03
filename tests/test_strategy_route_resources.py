from __future__ import annotations

import unittest

from lizhi_agent.actions import MainActionType, SquadActionType
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

    def test_high_score_uses_ice_box_before_quality_drops_too_far(self) -> None:
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
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_target_score_uses_ice_box_at_eighty_eight_freshness(self) -> None:
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
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

    def test_target_score_uses_ice_box_before_freshness_gap_opens(self) -> None:
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
        self.assertEqual(action.main.action, MainActionType.USE_RESOURCE)
        self.assertEqual(action.main.to_action()["resourceType"], "ICE_BOX")

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

    def test_reachable_ice_box_allows_quality_detour_at_target_score(self) -> None:
        strategy = self.make_strategy()
        state = GameState(
            frame=180,
            phase="NORMAL",
            player_id="1001",
            roles={"gateNodeId": "S14"},
            me=PlayerState(player_id="1001", status=ConvoyStatus.IDLE, station="S01", task_score_base=90, freshness=94),
            edges=[
                RouteEdge(id="D", start="S01", end="S14", distance=3),
                RouteEdge(id="R1", start="S01", end="S02", distance=12),
                RouteEdge(id="R2", start="S02", end="S14", distance=12),
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
                freshness=92,
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
        self.assertIsNone(action.main)

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
        self.assertIsNone(action.main)

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
            me=PlayerState(player_id="1001", team_id="RED", status=ConvoyStatus.IDLE, station="S01", task_score_base=130, squad_available=2, good_fruit=80),
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
        self.assertIsNone(wait_action.main)
        self.assertIsNone(wait_action.squad)

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
        self.assertIsNone(action.main)
        self.assertEqual(action.squad.action, SquadActionType.SQUAD_WEAKEN)
        payload = action.squad.to_action()
        self.assertEqual(payload["targetNodeId"], "S10")

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


if __name__ == "__main__":
    unittest.main()
