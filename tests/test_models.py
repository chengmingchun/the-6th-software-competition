import unittest

from lizhi_agent.models import parse_game_state


class ModelParsingTests(unittest.TestCase):
    def test_parse_edges_accepts_server_dist_field(self):
        start = {
            "round": 1,
            "durationRound": 600,
            "map": {"gameplay": {"roles": {"startNodeId": "S01", "gateNodeId": "S14", "terminalNodeIds": ["S15"]}}},
            "players": [{"playerId": "1001", "teamId": "RED"}],
            "nodes": [{"nodeId": "S01"}, {"nodeId": "S02"}],
            "edges": [{"id": "E01", "from": "S01", "to": "S02", "type": "ROAD", "dist": 30}],
        }
        inquire = {
            "round": 1,
            "phase": "NORMAL",
            "players": [{"playerId": "1001", "teamId": "RED", "state": "IDLE", "currentNodeId": "S01"}],
        }

        state = parse_game_state("1001", start, inquire)

        self.assertEqual(1, len(state.edges))
        self.assertEqual(30, state.edges[0].distance)


if __name__ == "__main__":
    unittest.main()
