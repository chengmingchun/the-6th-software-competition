from __future__ import annotations

import unittest

from tools.audit_metrics import audit_frame, new_audit


def inquire(
    player_state: str = "IDLE",
    resources: dict | None = None,
    freshness: float = 100.0,
    task_score: int = 0,
    verified: bool = False,
    phase: str = "NORMAL",
    current_node: str = "S03",
    edges: list[dict] | None = None,
):
    return {
        "msg_name": "inquire",
        "msg_data": {
            "phase": phase,
            "roles": {"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            "players": [
                {
                    "playerId": "1001",
                    "state": player_state,
                    "currentNodeId": current_node,
                    "routeEdgeId": "E1" if player_state == "MOVING" else None,
                    "freshness": freshness,
                    "taskScore": task_score,
                    "verified": verified,
                    "resources": resources or {},
                }
            ],
            "edges": edges if edges is not None else [{"fromNodeId": "S03", "toNodeId": "S14", "dist": 10}],
            "contests": [
                {"contestId": "c1", "contestType": "TASK", "targetNodeId": "S03", "taskId": "t1"},
                {"contestId": "c2", "contestType": "RESOURCE", "targetNodeId": "S03", "resourceType": "ICE_BOX"},
            ],
        },
    }


class AuditMetricsTest(unittest.TestCase):
    def test_idle_empty_is_counted_as_problem(self):
        audit = new_audit()
        audit_frame(audit, inquire(player_state="IDLE"), "1001", [])
        self.assertEqual(audit["emptyActionCount"], 1)
        self.assertEqual(audit["idleEmptyCount"], 1)
        self.assertEqual(audit["legalSystemWaitCount"], 0)

    def test_moving_empty_is_legal_system_wait(self):
        audit = new_audit()
        audit_frame(audit, inquire(player_state="MOVING"), "1001", [])
        self.assertEqual(audit["emptyActionCount"], 1)
        self.assertEqual(audit["idleEmptyCount"], 0)
        self.assertEqual(audit["legalSystemWaitCount"], 1)

    def test_delivered_empty_is_legal_system_wait(self):
        audit = new_audit()
        audit_frame(audit, inquire(player_state="DELIVERED"), "1001", [])
        self.assertEqual(audit["emptyActionCount"], 1)
        self.assertEqual(audit["idleEmptyCount"], 0)
        self.assertEqual(audit["legalSystemWaitCount"], 1)

    def test_horse_unused_is_not_flagged_while_moving(self):
        audit = new_audit()
        audit_frame(audit, inquire(player_state="MOVING", resources={"FAST_HORSE": 1}), "1001", [])
        self.assertEqual(audit["horseUnusedWhileMovingFrames"], 0)

    def test_ice_box_unused_is_not_flagged_while_moving_or_busy(self):
        audit = new_audit()
        audit_frame(audit, inquire(player_state="MOVING", resources={"ICE_BOX": 1}, freshness=80), "1001", [])
        audit_frame(audit, inquire(player_state="PROCESSING", resources={"ICE_BOX": 1}, freshness=80), "1001", [])
        self.assertEqual(audit["iceBoxUnusedLowFreshnessFrames"], 0)

    def test_high_value_abstain_is_counted(self):
        audit = new_audit()
        audit_frame(audit, inquire(), "1001", [{"action": "WINDOW_CARD", "contestId": "c1", "card": "ABSTAIN"}])
        self.assertEqual(audit["windowCardCount"], 1)
        self.assertEqual(audit["abstainCount"], 1)
        self.assertEqual(audit["highValueAbstainCount"], 1)

    def test_resource_usage_counts(self):
        audit = new_audit()
        audit_frame(audit, inquire(resources={"ICE_BOX": 1}, freshness=80, task_score=90), "1001", [{"action": "USE_RESOURCE", "resourceType": "ICE_BOX"}])
        self.assertEqual(audit["useResourceCount"], 1)
        self.assertEqual(audit["useIceBoxCount"], 1)
        self.assertEqual(audit["iceBoxUnusedLowFreshnessFrames"], 0)

    def test_unused_resource_warning_counts(self):
        audit = new_audit()
        audit_frame(audit, inquire(resources={"ICE_BOX": 1, "INTEL": 1}, freshness=80, task_score=90, current_node="S13", edges=[{"fromNodeId": "S13", "toNodeId": "S14", "dist": 4}]), "1001", [{"action": "MOVE", "targetNodeId": "S14"}])
        self.assertEqual(audit["iceBoxUnusedLowFreshnessFrames"], 1)
        self.assertEqual(audit["intelUnusedBeforeGateFrames"], 1)

    def test_intel_unused_warning_requires_legal_useful_target(self):
        audit = new_audit()
        audit_frame(
            audit,
            inquire(
                resources={"INTEL": 1},
                task_score=120,
                current_node="S03",
                edges=[{"fromNodeId": "S03", "toNodeId": "S08", "dist": 20}, {"fromNodeId": "S08", "toNodeId": "S14", "dist": 20}],
            ),
            "1001",
            [{"action": "MOVE", "targetNodeId": "S08"}],
        )
        self.assertEqual(audit["intelUnusedBeforeGateFrames"], 0)

    def test_intel_unused_warning_counts_gate_in_range(self):
        audit = new_audit()
        audit_frame(
            audit,
            inquire(
                resources={"INTEL": 1},
                task_score=120,
                current_node="S13",
                edges=[{"fromNodeId": "S13", "toNodeId": "S14", "dist": 4}],
            ),
            "1001",
            [{"action": "MOVE", "targetNodeId": "S14"}],
        )
        self.assertEqual(audit["intelUnusedBeforeGateFrames"], 1)

    def test_intel_unused_warning_ignores_early_gate_marker_that_may_expire(self):
        audit = new_audit()
        audit_frame(
            audit,
            inquire(
                resources={"INTEL": 1},
                task_score=120,
                current_node="S11",
                edges=[{"fromNodeId": "S11", "toNodeId": "S12", "dist": 3}, {"fromNodeId": "S12", "toNodeId": "S14", "dist": 4}],
            ),
            "1001",
            [{"action": "WAIT", "active": True}],
        )
        self.assertEqual(audit["intelUnusedBeforeGateFrames"], 0)

    def test_ice_box_unused_warning_ignores_low_score_normal_mid_freshness(self):
        audit = new_audit()
        audit_frame(audit, inquire(resources={"ICE_BOX": 1}, freshness=86.5, task_score=15), "1001", [{"action": "MOVE", "targetNodeId": "S04"}])
        self.assertEqual(audit["iceBoxUnusedLowFreshnessFrames"], 0)

    def test_ice_box_unused_warning_counts_rush_or_high_score(self):
        audit = new_audit()
        audit_frame(audit, inquire(resources={"ICE_BOX": 1}, freshness=86.5, task_score=120), "1001", [{"action": "MOVE", "targetNodeId": "S04"}])
        audit_frame(audit, inquire(resources={"ICE_BOX": 1}, freshness=86.5, task_score=15, phase="RUSH"), "1001", [{"action": "MOVE", "targetNodeId": "S04"}])
        self.assertEqual(audit["iceBoxUnusedLowFreshnessFrames"], 2)

    def test_guard_blocked_move_streak_counts_same_target(self):
        audit = new_audit()
        blocked = inquire()
        blocked["msg_data"]["actionResults"] = [
            {"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S10"}
        ]
        audit_frame(audit, blocked, "1001", [{"action": "MOVE", "targetNodeId": "S10"}])
        audit_frame(audit, blocked, "1001", [{"action": "MOVE", "targetNodeId": "S10"}])
        other = inquire()
        other["msg_data"]["actionResults"] = [
            {"playerId": "1001", "action": "MOVE", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "targetNodeId": "S11"}
        ]
        audit_frame(audit, other, "1001", [{"action": "MOVE", "targetNodeId": "S11"}])
        self.assertEqual(audit["guardBlockedMoveResultCount"], 3)
        self.assertEqual(audit["maxGuardBlockedMoveStreak"], 2)


if __name__ == "__main__":
    unittest.main()
