from __future__ import annotations

import unittest

from lizhi_agent.models import parse_game_state


class WaitFeedbackNormalizationTest(unittest.TestCase):
    def test_wait_feedback_targets_next_node(self) -> None:
        start = {
            "durationRound": 600,
            "players": [{"playerId": "1001"}],
            "nodes": [{"nodeId": "S09"}, {"nodeId": "S10"}],
            "edges": [{"edgeId": "E1", "fromNodeId": "S09", "toNodeId": "S10"}],
        }
        inquire = {
            "round": 286,
            "players": [{"playerId": "1001", "state": "WAITING", "currentNodeId": "S09", "nextNodeId": "S10"}],
            "actionResults": [{"playerId": "1001", "action": "WAIT", "accepted": False, "code": "MOVE_BLOCKED_BY_GUARD", "nodeId": "S09"}],
        }
        state = parse_game_state("1001", start, inquire)
        self.assertEqual(state.action_results[0]["action"], "MOVE")
        self.assertEqual(state.action_results[0]["targetNodeId"], "S10")


if __name__ == "__main__":
    unittest.main()
