"""Tests for the local competition server engine.

Covers:
- Fixed process node departure constraint
- S14 verify gate constraint
- Scout marker time reduction
- Error code fields on action results
"""

from __future__ import annotations

import unittest

from lizhi_server.engine import GameEngine, ScoutMarker, Player
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
        # Just test that the method exists and has correct signature
        self.assertTrue(hasattr(MatchRunner, "_recv_actions_pair"))
        self.assertTrue(callable(MatchRunner._recv_actions_pair))


if __name__ == "__main__":
    unittest.main()
