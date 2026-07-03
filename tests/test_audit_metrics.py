from __future__ import annotations

import unittest

from tools.audit_metrics import audit_frame, new_audit


def inquire(player_state: str = "IDLE", resources: dict | None = None, freshness: float = 100.0, task_score: int = 0, verified: bool = False):
    return {
        "msg_name": "inquire",
        "msg_data": {
            "roles": {"gateNodeId": "S14", "terminalNodeIds": ["S15"]},
            "players": [
                {
                    "playerId": "1001",
                    "state": player_state,
                    "currentNodeId": "S03",
                    "routeEdgeId": "E1" if player_state == "MOVING" else None,
                    "freshness": freshness,
                    "taskScore": task_score,
                    "verified": verified,
                    "resources": resources or {},
                }
            ],
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

    def test_horse_unused_is_not_flagged_while_moving(self):
        audit = new_audit()
        audit_frame(audit, inquire(player_state="MOVING", resources={"FAST_HORSE": 1}), "1001", [])
        self.assertEqual(audit["horseUnusedWhileMovingFrames"], 0)

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
        audit_frame(audit, inquire(resources={"ICE_BOX": 1, "INTEL": 1}, freshness=80, task_score=90), "1001", [{"action": "MOVE", "targetNodeId": "S04"}])
        self.assertEqual(audit["iceBoxUnusedLowFreshnessFrames"], 1)
        self.assertEqual(audit["intelUnusedBeforeGateFrames"], 1)


if __name__ == "__main__":
    unittest.main()
