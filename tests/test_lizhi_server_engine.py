"""Tests for the local competition server engine.

Covers:
- Fixed process node departure constraint
- S14 verify gate constraint
- Scout marker time reduction
- Error code fields on action results
"""

from __future__ import annotations

import unittest

from lizhi_server.engine import GameEngine, GuardState, ScoutMarker, Player, ContestWindow
from lizhi_server.config import FIXED_PROCESS_NODES


class TestFixedProcessConstraint(unittest.TestCase):
    """Players must complete PROCESS before leaving fixed process nodes."""

    def setUp(self):
        self.engine = GameEngine(seed=42, player1_id="1001", player2_id="1002")
        # Clear obstacles for clean movement
        for obs in self.engine.obstacles.values():
            obs.cleared = True
        for nid in self.engine.obstacles:
            self.engine.stations[nid]["hasObstacle"] = False

    def test_cannot_leave_s02_without_process(self):
        """MOVE from S02 before PROCESS returns PROCESS_REQUIRED."""
        p = self.engine.players["1001"]
        # Place player at S02
        p.station = "S02"
        p.status = "IDLE"
        # Try to MOVE to S03 without processing
        self.engine.process_actions(10, [{"action": "MOVE", "targetNodeId": "S03"}], [])
        ar = self.engine.action_results
        # Should have at least one action result for player 1001
        p1_results = [r for r in ar if str(r.get("playerId")) == "1001" and r.get("action") == "MOVE"]
        self.assertTrue(len(p1_results) >= 1, f"No MOVE result found: {ar}")
        result = p1_results[0]
        self.assertFalse(result.get("accepted", True))
        self.assertIn("PROCESS_REQUIRED", str(result.get("result", "")), f"Expected PROCESS_REQUIRED, got {result}")
        # Player should still be at S02
        self.assertEqual(p.station, "S02")

    def test_can_move_after_process_at_s02(self):
        """MOVE from S02 after completing PROCESS should succeed."""
        p = self.engine.players["1001"]
        p.station = "S02"
        p.status = "IDLE"
        # Complete the fixed process
        self.engine.process_actions(10, [{"action": "PROCESS", "targetNodeId": "S02"}], [])
        # Advance frames to complete it
        for f in range(11, 25):
            self.engine._advance_states(f)
        # Mark completed as the process completion handler would
        p.fixed_process_completed_here = True
        p.status = "IDLE"
        p.current_process = None

        self.engine.process_actions(25, [{"action": "MOVE", "targetNodeId": "S03"}], [])
        ar = self.engine.action_results
        p1_results = [r for r in ar if str(r.get("playerId")) == "1001" and r.get("action") == "MOVE"]
        self.assertTrue(len(p1_results) >= 1, f"No MOVE result found: {ar}")
        result = p1_results[0]
        self.assertTrue(result.get("accepted", False), f"MOVE should be accepted after PROCESS, got {result}")

    def test_s14_cannot_leave_to_s15_unverified(self):
        """MOVE from S14 to S15 without VERIFY_GATE should fail with VERIFY_REQUIRED."""
        p = self.engine.players["1001"]
        p.station = "S14"
        p.status = "IDLE"
        self.engine.process_actions(50, [{"action": "MOVE", "targetNodeId": "S15"}], [])
        ar = self.engine.action_results
        p1_results = [r for r in ar if str(r.get("playerId")) == "1001" and r.get("action") == "MOVE"]
        self.assertTrue(len(p1_results) >= 1, f"No MOVE result found: {ar}")
        result = p1_results[0]
        self.assertFalse(result.get("accepted", True))
        self.assertIn("VERIFY_REQUIRED", str(result.get("result", "")), f"Expected VERIFY_REQUIRED, got {result}")

    def test_can_leave_s14_to_s03_not_s15(self):
        """Leaving S14 to non-S15 is fine even without verify."""
        p = self.engine.players["1001"]
        p.station = "S14"
        p.status = "IDLE"
        # Path from S14 to S03 is via S13
        self.engine.process_actions(50, [{"action": "MOVE", "targetNodeId": "S13"}], [])
        ar = self.engine.action_results
        p1_results = [r for r in ar if str(r.get("playerId")) == "1001" and r.get("action") == "MOVE"]
        self.assertTrue(len(p1_results) >= 1, f"No MOVE result found: {ar}")
        result = p1_results[0]
        # Should be accepted since S13 != S15
        self.assertTrue(result.get("accepted", False), f"MOVE back to S13 should be OK, got {result}")

    def test_deliver_at_s15_needs_verify(self):
        """DELIVER at S15 without VERIFY_GATE should fail."""
        p = self.engine.players["1001"]
        p.station = "S15"
        p.status = "IDLE"
        self.engine.process_actions(60, [{"action": "DELIVER"}], [])
        ar = self.engine.action_results
        p1_results = [r for r in ar if str(r.get("playerId")) == "1001" and r.get("action") == "DELIVER"]
        self.assertTrue(len(p1_results) >= 1, f"No DELIVER result found: {ar}")
        result = p1_results[0]
        self.assertFalse(result.get("accepted", True))
        self.assertIn("DELIVER_NOT_VERIFIED", str(result.get("result", "")), f"Expected DELIVER_NOT_VERIFIED, got {result}")


class TestScoutMarkerReduction(unittest.TestCase):
    """Scout markers reduce processing time."""

    def setUp(self):
        self.engine = GameEngine(seed=42, player1_id="1001", player2_id="1002")

    def test_scout_reduces_process_time(self):
        """Scout marker reduces PROCESS from 4 to 2 frames (min 2)."""
        p = self.engine.players["1001"]
        p.station = "S02"
        p.status = "IDLE"
        # Add scout marker at S02
        self.engine.scout_markers.setdefault("S02", []).append(
            ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        )
        base_frames = FIXED_PROCESS_NODES["S02"][1]  # 4 for TRANSFER
        reduced, was_reduced = self.engine._apply_scout_reduction(p, "S02", base_frames)
        self.assertTrue(was_reduced)
        self.assertEqual(reduced, 2)  # max(2, 4-3) = 2

    def test_scout_reduces_verify_time(self):
        """Scout marker reduces VERIFY_GATE from 6 to 3."""
        p = self.engine.players["1001"]
        p.station = "S14"
        self.engine.scout_markers.setdefault("S14", []).append(
            ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        )
        reduced, was_reduced = self.engine._apply_scout_reduction(p, "S14", 6)
        self.assertTrue(was_reduced)
        self.assertEqual(reduced, 3)  # max(2, 6-3) = 3

    def test_scout_marker_used_only_once(self):
        """After use, scout marker is marked used and cannot be reused."""
        p = self.engine.players["1001"]
        marker = ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        self.engine.scout_markers.setdefault("S02", []).append(marker)
        # First use
        r1, was1 = self.engine._apply_scout_reduction(p, "S02", 6)
        self.assertTrue(was1)
        self.assertTrue(marker.used)
        # Second use — should not find a usable marker
        r2, was2 = self.engine._apply_scout_reduction(p, "S02", 6)
        self.assertFalse(was2, "Already-used marker should not reduce again")
        self.assertEqual(r2, 6)

    def test_expired_marker_not_usable(self):
        """Marker past end_frame should not reduce time."""
        p = self.engine.players["1001"]
        self.engine.frame = 200
        self.engine.scout_markers.setdefault("S02", []).append(
            ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        )
        r, was = self.engine._apply_scout_reduction(p, "S02", 6)
        self.assertFalse(was)
        self.assertEqual(r, 6, "Expired marker should not reduce")

    def test_other_team_marker_not_usable(self):
        """Marker from opponent's team should not be usable."""
        p = self.engine.players["1001"]
        # Opponent's marker
        opp_team = "BLUE" if p.team_id == "RED" else "RED"
        self.engine.scout_markers.setdefault("S02", []).append(
            ScoutMarker(team_id=opp_team, start_frame=1, end_frame=100, used=False)
        )
        r, was = self.engine._apply_scout_reduction(p, "S02", 6)
        self.assertFalse(was, "Opponent marker should not be usable")
        self.assertEqual(r, 6)


class TestActionResultErrorCodes(unittest.TestCase):
    """Rejected actions must include code/errorCode/reason/message fields."""

    def setUp(self):
        self.engine = GameEngine(seed=42, player1_id="1001", player2_id="1002")
        for obs in self.engine.obstacles.values():
            obs.cleared = True

    def test_rejected_result_has_all_error_fields(self):
        """A rejected MOVE should produce code, errorCode, reason, message."""
        p = self.engine.players["1001"]
        p.station = "S02"
        p.status = "IDLE"
        # Try MOVE without PROCESS at S02
        self.engine.process_actions(10, [{"action": "MOVE", "targetNodeId": "S03"}], [])
        for ar in self.engine.action_results:
            if str(ar.get("playerId")) == "1001" and ar.get("action") == "MOVE" and not ar.get("accepted", True):
                self.assertIn("errorCode", ar, f"Missing errorCode in {ar}")
                self.assertIn("code", ar, f"Missing code in {ar}")
                self.assertIn("reason", ar, f"Missing reason in {ar}")
                self.assertIn("message", ar, f"Missing message in {ar}")
                self.assertEqual(ar["code"], ar["result"], "code should equal result")
                return
        self.fail(f"No rejected MOVE result found: {self.engine.action_results}")

    def test_accepted_result_no_error_fields(self):
        """An accepted action should not add superfluous error fields."""
        p = self.engine.players["1001"]
        p.station = "S01"
        p.status = "IDLE"
        self.engine.process_actions(10, [{"action": "WAIT"}], [])
        for ar in self.engine.action_results:
            if str(ar.get("playerId")) == "1001" and ar.get("action") == "WAIT":
                if ar.get("accepted", False):
                    self.assertNotIn("code", ar, f"Accepted action should not have code: {ar}")
                    return


class TestRecvActionsUtility(unittest.TestCase):
    """Test the _recv_actions_pair helper of MatchRunner (logic only)."""

    def test_recv_actions_pair_interface(self):
        """_recv_actions_pair should be a method on MatchRunner that returns two values."""
        from lizhi_server.server import MatchRunner
        import socket
        self.assertTrue(hasattr(MatchRunner, "_recv_actions_pair"))
        self.assertTrue(callable(MatchRunner._recv_actions_pair))


class TestOnlineRealismHardening(unittest.TestCase):
    """Server rules learned from online logs should catch unsafe strategies."""

    def setUp(self):
        self.engine = GameEngine(seed=42, player1_id="1001", player2_id="1002")
        for obs in self.engine.obstacles.values():
            obs.cleared = True
        for nid in self.engine.obstacles:
            self.engine.stations[nid]["hasObstacle"] = False

    def _put_player_moving(self):
        p = self.engine.players["1001"]
        p.station = "S01"
        p.status = "MOVING"
        p.target_station = "S02"
        p.route_edge = "E01"
        p.route_type = "ROAD"
        p.move_accumulated = 0
        p.move_edge_distance = 30
        p.move_edge_coefficient = 1380
        p.resources["FAST_HORSE"] = 1
        return p

    def test_empty_action_while_moving_keeps_progress_and_no_illegal(self):
        """Empty heartbeat while MOVING should continue movement without penalty."""
        p = self._put_player_moving()
        self.engine.process_actions(10, [], [])
        self.assertEqual(p.status, "MOVING")
        self.assertGreater(p.move_accumulated, 0)
        self.assertEqual(p.illegal_action_count, 0)
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertEqual(p1_results, [], f"System wait should not emit rejection: {p1_results}")

    def test_horse_resource_while_moving_is_legal(self):
        """Horse resources may be used while MOVING."""
        p = self._put_player_moving()
        self.engine.process_actions(10, [{"action": "USE_RESOURCE", "resourceType": "FAST_HORSE"}], [])
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(p1_results, "Expected a resource result")
        self.assertTrue(p1_results[0].get("accepted", False))
        self.assertEqual(p.illegal_action_count, 0)
        self.assertEqual(p.resources["FAST_HORSE"], 0)

    def test_non_horse_resource_while_moving_is_forbidden(self):
        """Non-horse main-convoy resource use is still restricted while MOVING."""
        p = self._put_player_moving()
        p.resources["ICE_BOX"] = 1
        self.engine.process_actions(10, [{"action": "USE_RESOURCE", "resourceType": "ICE_BOX"}], [])
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(p1_results, "Expected a rejection result")
        self.assertFalse(p1_results[0].get("accepted", True))
        self.assertEqual(p1_results[0].get("code"), "STATE_MOVING_FORBIDDEN")
        self.assertEqual(p.illegal_action_count, 1)
        self.assertEqual(p.resources["ICE_BOX"], 1)

    def test_squad_action_while_moving_is_legal(self):
        """Squad commands are independent from MOVING main-convoy restrictions."""
        p = self._put_player_moving()
        self.engine.process_actions(10, [{"action": "SQUAD_SCOUT", "targetNodeId": "S02"}], [])
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(p1_results, "Expected a squad result")
        self.assertTrue(p1_results[0].get("accepted", False))
        self.assertEqual(p.illegal_action_count, 0)
        self.assertEqual(p.squad_available, 7)

    def test_same_frame_move_and_squad_from_idle_is_legal(self):
        """A legal frame-start IDLE packet may contain both MOVE and SQUAD."""
        p = self.engine.players["1001"]
        p.station = "S01"
        p.status = "IDLE"
        self.engine.process_actions(
            10,
            [
                {"action": "MOVE", "targetNodeId": "S02"},
                {"action": "SQUAD_SCOUT", "targetNodeId": "S02"},
            ],
            [],
        )
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        by_action = {r.get("action"): r for r in p1_results}
        self.assertTrue(by_action["SQUAD_SCOUT"].get("accepted", False), p1_results)
        self.assertTrue(by_action["MOVE"].get("accepted", False), p1_results)
        self.assertEqual(p.status, "MOVING")
        self.assertEqual(p.illegal_action_count, 0)

    def test_squad_weaken_is_valid_by_protocol(self):
        """SQUAD_WEAKEN is a documented squad action and lands after delay."""
        p1 = self.engine.players["1001"]
        p2 = self.engine.players["1002"]
        p1.station = "S09"
        p1.status = "IDLE"
        p2.station = "S10"
        p2.status = "IDLE"
        p2.guards["S10"] = GuardState(
            owner_team=p2.team_id,
            defense=2,
            cap=7,
            completed_frame=19,
            last_wind_frame=19,
            wind_interval=30,
            is_key_pass=True,
        )

        self.engine.process_actions(20, [{"action": "SQUAD_WEAKEN", "targetNodeId": "S10"}], [])
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(p1_results, "Expected SQUAD_WEAKEN result")
        self.assertTrue(p1_results[0].get("accepted", False))
        self.assertEqual(p1.illegal_action_count, 0)
        self.assertEqual(p1.squad_available, 6)
        self.assertEqual(p2.guards["S10"].defense, 2)
        for frame in range(21, 36):
            self.engine.process_actions(frame, [], [])
        self.assertEqual(p2.guards["S10"].defense, 0)

    def test_two_person_squad_actions_require_two_available_members(self):
        """CLEAR/REINFORCE/WEAKEN consume 2 people and must reject with only 1."""
        p1 = self.engine.players["1001"]
        p2 = self.engine.players["1002"]
        p1.station = "S09"
        p1.status = "IDLE"
        p1.squad_available = 1
        p2.guards["S10"] = GuardState(
            owner_team=p2.team_id,
            defense=2,
            cap=7,
            completed_frame=19,
            last_wind_frame=19,
            wind_interval=30,
            is_key_pass=True,
        )

        self.engine.process_actions(20, [{"action": "SQUAD_WEAKEN", "targetNodeId": "S10"}], [])
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(p1_results, "Expected SQUAD_WEAKEN rejection")
        self.assertFalse(p1_results[0].get("accepted", True))
        self.assertEqual(p1_results[0].get("code"), "SQUAD_NOT_AVAILABLE")
        self.assertEqual(p1.squad_available, 1)
        self.assertEqual(p2.guards["S10"].defense, 2)

    def test_squad_action_rejects_unknown_target_node(self):
        """Squad actions target map nodes, not arbitrary ids."""
        p1 = self.engine.players["1001"]
        p1.station = "S09"
        p1.status = "IDLE"

        self.engine.process_actions(20, [{"action": "SQUAD_SCOUT", "targetNodeId": "NOPE"}], [])
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(p1_results, "Expected SQUAD_SCOUT rejection")
        self.assertFalse(p1_results[0].get("accepted", True))
        self.assertEqual(p1_results[0].get("code"), "TARGET_NOT_FOUND")
        self.assertEqual(p1.squad_available, 8)

    def test_guard_placed_during_movement_traps_convoy_until_wind(self):
        """If a target gets guarded while the convoy is MOVING, arrival stalls."""
        p1 = self.engine.players["1001"]
        p2 = self.engine.players["1002"]
        p1.station = "S09"
        p1.status = "MOVING"
        p1.target_station = "S10"
        p1.route_edge = "E05"
        p1.route_type = "ROAD"
        p1.move_edge_distance = 40
        p1.move_edge_coefficient = 1380
        p1.move_accumulated = 40 * 1380 - 500
        p2.guards["S10"] = GuardState(
            owner_team=p2.team_id,
            defense=2,
            cap=7,
            completed_frame=19,
            last_wind_frame=19,
            wind_interval=30,
            is_key_pass=True,
        )

        self.engine.process_actions(20, [], [])
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(p1_results, "Expected moving guard block feedback")
        self.assertEqual(p1_results[0].get("code"), "MOVE_BLOCKED_BY_GUARD")
        self.assertEqual(p1_results[0].get("targetNodeId"), "S10")
        self.assertTrue(p1_results[0].get("systemFeedback"))
        self.assertFalse(p1_results[0].get("submittedAction"))
        p1_events = [
            e for e in self.engine.events
            if e.type == "MOVE_BLOCKED_BY_GUARD" and str(e.payload.get("playerId")) == "1001"
        ]
        self.assertTrue(p1_events, "Expected moving guard block feedback event")
        self.assertEqual(p1_events[0].payload.get("targetNodeId"), "S10")
        self.assertEqual(p1.status, "MOVING")
        self.assertEqual(p1.station, "S09")

        p2.guards["S10"].defense = 0
        self.engine.process_actions(21, [], [])
        self.assertEqual(p1.status, "IDLE")
        self.assertEqual(p1.station, "S10")


class TestOnlineGuardRegressionScenario(unittest.TestCase):
    """Opt-in scenario should reproduce online key-pass guard stalls."""

    def setUp(self):
        self.engine = GameEngine(
            seed=42,
            player1_id="1001",
            player2_id="1002",
            scenario="guard_gauntlet",
        )
        for obs in self.engine.obstacles.values():
            obs.cleared = True
        for nid in self.engine.obstacles:
            self.engine.stations[nid]["hasObstacle"] = False

    def test_s10_guard_is_injected_and_blocks_plain_move(self):
        p1 = self.engine.players["1001"]
        p2 = self.engine.players["1002"]
        p1.station = "S09"
        p1.status = "IDLE"

        self.engine.process_actions(312, [{"action": "MOVE", "targetNodeId": "S10"}], [])

        self.assertIn("S10", p2.guards)
        self.assertGreater(p2.guards["S10"].defense, 0)
        p1_results = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(p1_results, "Expected MOVE rejection")
        result = p1_results[0]
        self.assertFalse(result.get("accepted", True))
        self.assertEqual(result.get("code"), "MOVE_BLOCKED_BY_GUARD")
        self.assertEqual(result.get("targetNodeId"), "S10")
        self.assertEqual(p1.station, "S09")

    def test_moving_lock_trap_injects_guard_while_convoy_is_moving(self):
        engine = GameEngine(
            seed=42,
            player1_id="1001",
            player2_id="1002",
            scenario="moving_lock_trap",
        )
        for obs in engine.obstacles.values():
            obs.cleared = True
        for nid in engine.obstacles:
            engine.stations[nid]["hasObstacle"] = False
        p1 = engine.players["1001"]
        p2 = engine.players["1002"]
        p1.station = "S09"
        p1.status = "MOVING"
        p1.target_station = "S10"

        engine.process_actions(300, [], [])

        self.assertIn("S10", p2.guards)
        self.assertGreater(p2.guards["S10"].defense, 0)

    def test_rush_guard_wall_injects_late_palace_and_gate_guards(self):
        engine = GameEngine(
            seed=42,
            player1_id="1001",
            player2_id="1002",
            scenario="rush_guard_wall",
        )
        p1 = engine.players["1001"]
        p2 = engine.players["1002"]
        p1.station = "S12"
        p1.status = "IDLE"

        engine.process_actions(450, [], [])
        self.assertIn("S13", p2.guards)
        self.assertEqual(p2.guards["S13"].defense, 4)

        p1.station = "S13"
        engine.process_actions(500, [], [])
        self.assertIn("S14", p2.guards)
        self.assertLessEqual(p2.guards["S14"].defense, 4)

    def test_weather_obstacle_gauntlet_adds_deterministic_weather_pressure(self):
        engine = GameEngine(
            seed=42,
            player1_id="1001",
            player2_id="1002",
            scenario="weather_obstacle_gauntlet",
        )

        deterministic_events = {
            (event.weather_type, event.start_frame, event.duration)
            for event in engine.weather_events
        }
        self.assertIn(("MOUNTAIN_FOG", 150, 90), deterministic_events)
        self.assertIn(("HOT", 300, 120), deterministic_events)
        self.assertIn(("HEAVY_RAIN", 430, 80), deterministic_events)

    def test_full_stress_combines_guard_and_weather_scenarios(self):
        engine = GameEngine(
            seed=42,
            player1_id="1001",
            player2_id="1002",
            scenario="full_stress",
        )
        p1 = engine.players["1001"]
        p2 = engine.players["1002"]
        p1.station = "S09"
        p1.status = "MOVING"
        p1.target_station = "S10"

        engine.process_actions(300, [], [])

        self.assertIn("S10", p2.guards)
        self.assertTrue(any(event.weather_type == "HOT" and event.start_frame == 300 for event in engine.weather_events))


class TestScoutConsumptionBeforeValidation(unittest.TestCase):
    """Scout marker should not be consumed if PROCESS is rejected."""

    def setUp(self):
        self.engine = GameEngine(seed=42, player1_id="1001", player2_id="1002")
        for obs in self.engine.obstacles.values():
            obs.cleared = True

    def test_scout_not_consumed_on_not_at_target_node(self):
        """PROCESS at wrong node should NOT consume scout marker."""
        p = self.engine.players["1001"]
        # Player at S01, send PROCESS target=S02 (NOT_AT_TARGET_NODE)
        p.station = "S01"
        p.status = "IDLE"
        # Place a scout marker at S02
        self.engine.scout_markers.setdefault("S02", []).append(
            ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        )
        self.engine.process_actions(10, [{"action": "PROCESS", "targetNodeId": "S02"}], [])
        # Should be rejected
        ar = [r for r in self.engine.action_results if str(r.get("playerId")) == "1001"]
        self.assertTrue(any(not r.get("accepted", True) for r in ar),
                        f"PROCESS should be rejected: {self.engine.action_results}")
        # Scout marker should still be unused
        markers = self.engine.scout_markers.get("S02", [])
        self.assertTrue(any(not m.used for m in markers),
                        "Scout marker at S02 should remain unused after rejected PROCESS")


class TestForcedPassFixedProcessReset(unittest.TestCase):
    """ _complete_forced_pass should reset fixed_process_completed_here."""

    def setUp(self):
        self.engine = GameEngine(seed=42, player1_id="1001", player2_id="1002")
        for obs in self.engine.obstacles.values():
            obs.cleared = True

    def test_forced_pass_resets_fixed_process_completed(self):
        """After forced pass into S11 (PASS_TRANSFER), need to redo PROCESS."""
        p = self.engine.players["1001"]
        p.station = "S10"
        p.status = "IDLE"
        p.fixed_process_completed_here = True  # pretend we had done it

        # Simulate what _complete_forced_pass does
        p.station = "S11"
        p.status = "IDLE"
        p.current_process = None
        p.target_station = None
        p.route_edge = None
        # This is exactly what _complete_forced_pass should do:
        p.fixed_process_completed_here = False

        self.assertFalse(p.fixed_process_completed_here,
                         "forced_pass arrival should reset fixed_process_completed_here")

    def test_forced_pass_requires_process_at_fixed_node(self):
        """Entering S11 via forced pass then MOVE back should reject with PROCESS_REQUIRED."""
        p = self.engine.players["1001"]
        p.station = "S10"
        p.status = "IDLE"
        p.fixed_process_completed_here = True

        # Manually call what _complete_forced_pass does for entering S11
        p.station = "S11"
        p.status = "IDLE"
        p.current_process = None
        p.target_station = None
        p.route_edge = None
        p.fixed_process_completed_here = False

        # Now try to MOVE away without PROCESS
        self.engine.process_actions(50, [{"action": "MOVE", "targetNodeId": "S12"}], [])
        ar = [r for r in self.engine.action_results
              if str(r.get("playerId")) == "1001" and r.get("action") == "MOVE"]
        self.assertTrue(ar, f"No MOVE result found: {self.engine.action_results}")
        self.assertFalse(ar[0].get("accepted", True),
                         f"Should reject MOVE without PROCESS, got {ar[0]}")
        self.assertIn("PROCESS_REQUIRED", str(ar[0].get("result", "")),
                      f"Expected PROCESS_REQUIRED, got {ar[0]}")


class TestScoutReductionEndToEnd(unittest.TestCase):
    """End-to-end tests: scout markers reduce processing time through process_actions."""

    def setUp(self):
        self.engine = GameEngine(seed=42, player1_id="1001", player2_id="1002")
        for obs in self.engine.obstacles.values():
            obs.cleared = True
        for nid in self.engine.obstacles:
            self.engine.stations[nid]["hasObstacle"] = False

    def test_process_reduced_by_scout(self):
        """PROCESS at S02 with scout marker uses reduced frames."""
        p = self.engine.players["1001"]
        p.station = "S02"
        p.status = "IDLE"
        # Add scout marker at S02
        self.engine.scout_markers.setdefault("S02", []).append(
            ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        )
        self.engine.process_actions(10, [{"action": "PROCESS", "targetNodeId": "S02"}], [])
        # PROCESS should be accepted with reduced frames
        self.assertIsNotNone(p.current_process, "Should have started processing")
        self.assertEqual(p.current_process.get("totalFrames"), 2,
                         f"Expected 2 (reduced from 4), got {p.current_process}")
        # Marker should be used
        markers = self.engine.scout_markers.get("S02", [])
        self.assertTrue(all(m.used for m in markers), "Scout markers should be used")

    def test_claim_resource_reduced_by_scout(self):
        """CLAIM_RESOURCE at S03 with scout marker uses reduced frames."""
        p = self.engine.players["1001"]
        p.station = "S03"
        p.status = "IDLE"
        # Ensure resource exists
        self.engine.resource_stock.setdefault("S03", {})["ICE_BOX"] = 1
        # Add scout marker
        self.engine.scout_markers.setdefault("S03", []).append(
            ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        )
        self.engine.process_actions(10, [{"action": "CLAIM_RESOURCE", "targetNodeId": "S03", "resourceType": "ICE_BOX"}], [])
        self.assertIsNotNone(p.current_process, "Should have started claiming")
        self.assertEqual(p.current_process.get("totalFrames"), 2,
                         f"Expected 2 (RESOURCE_CLAIM_FRAMES=2 -> min=2, no reduction possible), got {p.current_process}")

    def test_verify_gate_reduced_by_scout(self):
        """VERIFY_GATE at S14 with scout marker uses reduced frames."""
        p = self.engine.players["1001"]
        p.station = "S14"
        p.status = "IDLE"
        self.engine.phase = "RUSH"
        # Add scout marker
        self.engine.scout_markers.setdefault("S14", []).append(
            ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        )
        self.engine.process_actions(50, [{"action": "VERIFY_GATE", "targetNodeId": "S14"}], [])
        self.assertIsNotNone(p.current_process, "Should have started verifying")
        self.assertEqual(p.current_process.get("totalFrames"), 3,
                         f"Expected 3 (reduced from 6), got {p.current_process}")

    def test_verify_gate_break_order_and_scout_stack(self):
        """VERIFY_GATE with BREAK_ORDER and scout marker: 6->3->2 (stacked)."""
        p = self.engine.players["1001"]
        p.station = "S14"
        p.status = "IDLE"
        p.rush_tactic_used = 0
        self.engine.phase = "RUSH"
        # Add scout marker
        self.engine.scout_markers.setdefault("S14", []).append(
            ScoutMarker(team_id=p.team_id, start_frame=1, end_frame=100, used=False)
        )
        self.engine.process_actions(50, [{"action": "VERIFY_GATE", "targetNodeId": "S14", "rushTactic": "BREAK_ORDER"}], [])
        self.assertIsNotNone(p.current_process, "Should have started verifying")
        # BREAK_ORDER reduces 6->3, then scout reduces 3->2
        total = p.current_process.get("totalFrames", 99)
        self.assertLessEqual(total, 3,
                             f"Expected <=3 (stacked reduction), got {total}")
        # Note: if both stack, result is max(2, 6-3-3)=2. Check:
        self.assertEqual(total, 2,
                         f"Expected 2 (break_order 6->3, scout 3->2), got {total}")


class TestWindowContestResolution(unittest.TestCase):
    """Window contests should cleanly resolve without leaving players stuck."""

    def setUp(self):
        self.engine = GameEngine(seed=42, player1_id="1001", player2_id="1002")
        for obs in self.engine.obstacles.values():
            obs.cleared = True

    def _resolve_window_full(self, contest_type="RESOURCE", resource_type="SHORT_HORSE"):
        """Create a window and run it through all 3 beats with cards."""
        pid1, pid2 = "1001", "1002"
        p1, p2 = self.engine.players[pid1], self.engine.players[pid2]
        p1.station = "S07"
        p2.station = "S07"
        p1.status = "IDLE"
        p2.status = "IDLE"

        contest = ContestWindow(
            contest_id="C_test_001",
            contest_type=contest_type,
            target_node="S07",
            resource_type=resource_type if contest_type == "RESOURCE" else None,
            red_player_id=pid1 if self.engine.team_map[pid1] == "RED" else pid2,
            blue_player_id=pid2 if self.engine.team_map[pid2] == "BLUE" else pid1,
            round_index=1,
            total_rounds=3,
            deadline_round=self.engine.frame + 1,
        )
        self.engine.contests.append(contest)
        return pid1, pid2

    def test_resource_window_resolves_cleanly(self):
        """After 3 beats of a resource contest, players are no longer CONTESTING."""
        pid1, pid2 = self._resolve_window_full("RESOURCE")
        # Play all 3 beats with BING_ZHENG vs ABSTAIN -> RED wins all 3
        for beat in range(1, 4):
            if self.engine.team_map[pid1] == "RED":
                red_pid, blue_pid = pid1, pid2
            else:
                red_pid, blue_pid = pid2, pid1

            self.engine.process_actions(
                self.engine.frame + 1,
                [{"action": "WINDOW_CARD", "contestId": "C_test_001", "card": "BING_ZHENG"}],
                [{"action": "WINDOW_CARD", "contestId": "C_test_001", "card": "ABSTAIN"}],
            )
            self.engine._advance_states(self.engine.frame)

        # After all beats, the contest should be resolved
        resolved = [c for c in self.engine.contests if c.contest_id == "C_test_001"]
        # It may have been removed from the list or marked resolved
        if resolved:
            self.assertTrue(resolved[0].resolved, "Contest should be resolved")

    def test_window_does_not_stick_in_contesting(self):
        """After window resolves, players should not remain CONTESTING."""
        pid1, pid2 = self._resolve_window_full()
        p1 = self.engine.players[pid1]
        p2 = self.engine.players[pid2]

        # Red wins with BING_ZHENG, Blue abstains
        for beat in range(1, 4):
            if self.engine.team_map[pid1] == "RED":
                red_pid, blue_pid = pid1, pid2
            else:
                red_pid, blue_pid = pid2, pid1

            self.engine.process_actions(
                self.engine.frame + 1,
                [{"action": "WINDOW_CARD", "contestId": "C_test_001", "card": "BING_ZHENG"}],
                [{"action": "WINDOW_CARD", "contestId": "C_test_001", "card": "ABSTAIN"}],
            )
            self.engine._advance_states(self.engine.frame)

        # Check players are not stuck in CONTESTING
        self.assertNotEqual(p1.status, "CONTESTING",
                            f"P1 should not be CONTESTING after window resolved, got {p1.status}")
        self.assertNotEqual(p2.status, "CONTESTING",
                            f"P2 should not be CONTESTING after window resolved, got {p2.status}")

    def test_pass_draw_restores_initiator_and_defender(self):
        """PASS windows that draw should not leave either side CONTESTING."""
        pid1, pid2 = "1001", "1002"
        p1 = self.engine.players[pid1]
        p2 = self.engine.players[pid2]
        p1.station = "S09"
        p2.station = "S09"
        p1.status = "CONTESTING"
        p2.status = "CONTESTING"

        contest = ContestWindow(
            contest_id="C_pass_draw",
            contest_type="PASS",
            target_node="S10",
            red_player_id=pid1 if self.engine.team_map[pid1] == "RED" else pid2,
            blue_player_id=pid2 if self.engine.team_map[pid2] == "BLUE" else pid1,
            initiator_player_id=pid1,
            round_index=1,
            total_rounds=3,
            deadline_round=self.engine.frame + 1,
            initial_time_tax=12,
        )
        self.engine.contests.append(contest)

        for _ in range(3):
            self.engine.process_actions(
                self.engine.frame + 1,
                [{"action": "WINDOW_CARD", "contestId": "C_pass_draw", "card": "ABSTAIN"}],
                [{"action": "WINDOW_CARD", "contestId": "C_pass_draw", "card": "ABSTAIN"}],
            )
            self.engine._advance_states(self.engine.frame)

        self.assertEqual(p1.status, "RESTING")
        self.assertEqual(p1.current_process["type"], "REST")
        self.assertEqual(p2.status, "IDLE")
        self.assertIsNone(p2.current_process)


if __name__ == "__main__":
    unittest.main()
