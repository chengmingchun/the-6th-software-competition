from __future__ import annotations

import unittest

from tools.audit_gate import GateRule, default_rules, parse_custom_rule, run_gate


class AuditGateTest(unittest.TestCase):
    def test_custom_rule_parser(self):
        rule = parse_custom_rule("b.highValueAbstainCount<=2:window passive")
        self.assertEqual(rule.side, "b")
        self.assertEqual(rule.metric, "highValueAbstainCount")
        self.assertEqual(rule.op, "<=")
        self.assertEqual(rule.threshold, 2)
        self.assertEqual(rule.message, "window passive")

    def test_gate_passes_good_rows(self):
        rows = [
            {
                "b_delivered": "True",
                "b_idleEmptyCount": "0",
                "b_rejectedActionCount": "0",
                "b_highValueAbstainCount": "1",
                "b_iceBoxUnusedLowFreshnessFrames": "0",
                "b_horseUnusedWhileMovingFrames": "0",
                "b_intelUnusedBeforeGateFrames": "0",
            },
            {
                "b_delivered": "True",
                "b_idleEmptyCount": "0",
                "b_rejectedActionCount": "0",
                "b_highValueAbstainCount": "1",
                "b_iceBoxUnusedLowFreshnessFrames": "0",
                "b_horseUnusedWhileMovingFrames": "0",
                "b_intelUnusedBeforeGateFrames": "0",
            },
        ]
        ok, lines = run_gate(rows, default_rules("b"))
        self.assertTrue(ok, "\n".join(lines))

    def test_gate_fails_bad_rows(self):
        rows = [
            {
                "b_delivered": "False",
                "b_idleEmptyCount": "5",
                "b_rejectedActionCount": "3",
                "b_highValueAbstainCount": "9",
                "b_iceBoxUnusedLowFreshnessFrames": "12",
                "b_horseUnusedWhileMovingFrames": "0",
                "b_intelUnusedBeforeGateFrames": "0",
            }
        ]
        ok, lines = run_gate(rows, default_rules("b"))
        self.assertFalse(ok)
        text = "\n".join(lines)
        self.assertIn("FAIL", text)
        self.assertIn("送达率不足", text)
        self.assertIn("IDLE 空动作过多", text)

    def test_custom_rule_execution(self):
        rows = [{"a_totalScore": "800"}, {"a_totalScore": "700"}]
        ok, lines = run_gate(rows, [GateRule("a", "totalScore", ">=", 760, "score too low")])
        self.assertFalse(ok)
        self.assertIn("actual=750.00", lines[0])


if __name__ == "__main__":
    unittest.main()
