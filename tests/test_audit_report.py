from __future__ import annotations

import unittest

from tools.audit_report import avg, pct, side_warnings, winner_summary


class AuditReportTest(unittest.TestCase):
    def test_avg_and_pct(self):
        rows = [
            {"a_totalScore": "700", "a_delivered": "True"},
            {"a_totalScore": "800", "a_delivered": "False"},
        ]
        self.assertEqual(avg(rows, "a", "totalScore"), 750)
        self.assertEqual(pct(rows, "a", "delivered"), 50)

    def test_warnings_include_idle_empty_and_resources(self):
        rows = [
            {
                "a_idleEmptyCount": "2",
                "a_highValueAbstainCount": "3",
                "a_rejectedActionCount": "0",
                "a_maxGuardBlockedMoveStreak": "5",
                "a_iceBoxUnusedLowFreshnessFrames": "4",
                "a_horseUnusedWhileMovingFrames": "0",
                "a_intelUnusedBeforeGateFrames": "0",
                "a_taskScore": "80",
                "a_freshness": "70",
            }
        ]
        text = "\n".join(side_warnings(rows, "a"))
        self.assertIn("IDLE empty actions", text)
        self.assertIn("High-value window abstains", text)
        self.assertIn("Repeated MOVE_BLOCKED_BY_GUARD", text)
        self.assertIn("ICE_BOX", text)
        self.assertIn("task score is below the safety floor", text)
        self.assertIn("freshness is low", text)

    def test_winner_summary(self):
        rows = [
            {"winnerBotDir": "/tmp/root", "a_botDir": "/tmp/root", "b_botDir": "/tmp/claude"},
            {"winnerBotDir": "/tmp/claude", "a_botDir": "/tmp/root", "b_botDir": "/tmp/claude"},
            {"winnerBotDir": "DRAW", "a_botDir": "/tmp/root", "b_botDir": "/tmp/claude"},
        ]
        self.assertEqual(winner_summary(rows, "root", "claude"), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
