#!/usr/bin/env python3
"""Competition client entrypoint.

Usage:
    python3 main.py <playerId> [host] [port]

The exact protocol should be aligned with the official communication document.
This baseline supports JSON-lines over TCP when host/port are provided and
JSON-lines over stdin/stdout for local debugging.
"""

from __future__ import annotations

import sys

from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.protocol import JsonLineClient
from lizhi_agent.strategy import BaselineStrategy


def main(argv: list[str]) -> int:
    player_id = argv[1] if len(argv) > 1 and argv[1] else "player0"
    host = argv[2] if len(argv) > 2 and argv[2] else None
    port = int(argv[3]) if len(argv) > 3 and argv[3] else None

    logger = DecisionLogger(player_id=player_id)
    config = StrategyConfig.default()
    strategy = BaselineStrategy(player_id=player_id, config=config, logger=logger)
    client = JsonLineClient(player_id=player_id, strategy=strategy, logger=logger)

    if host and port:
        return client.run_socket(host, port)
    return client.run_stdio()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
