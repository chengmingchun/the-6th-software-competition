#!/usr/bin/env python3
"""Competition client entrypoint.

Usage:
    python3 main.py <playerId> <host> <port>

When host/port are present the client uses the official TCP protocol:
5 decimal length bytes plus a UTF-8 JSON body.  Without host/port it falls back
to JSON-lines on stdin/stdout for local strategy fixtures.
"""

from __future__ import annotations

import sys

from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.protocol import CompetitionClient
from lizhi_agent.strategy import BaselineStrategy


def main(argv: list[str]) -> int:
    player_id = argv[1] if len(argv) > 1 and argv[1] else "player0"
    host = argv[2] if len(argv) > 2 and argv[2] else None
    port = int(argv[3]) if len(argv) > 3 and argv[3] else None

    logger = DecisionLogger(player_id=player_id)
    config = StrategyConfig.default()
    strategy = BaselineStrategy(player_id=player_id, config=config, logger=logger)
    client = CompetitionClient(player_id=player_id, strategy=strategy, logger=logger)

    if host and port:
        return client.run_socket(host, port)
    return client.run_stdio()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
