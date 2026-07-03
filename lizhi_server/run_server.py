#!/usr/bin/env python3
"""Run the local competition server for 一骑红尘：荔枝争运战.

This server simulates a complete match, accepting two TCP clients and running
the full 600-frame game loop.

Usage:
    python -m lizhi_server.run_server [--port PORT] [--seed SEED]

Default:
    port=30000, seed=random
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import threading
import time

from lizhi_server.server import MatchRunner, _log, SERVER_PORT


class GameServer:
    """Listens for two clients and starts a match."""

    def __init__(self, host: str = "0.0.0.0", port: int = SERVER_PORT,
                 seed: int | None = None) -> None:
        self.host = host
        self.port = port
        self.seed = seed or random.randint(0, 999999)
        self.match_counter = 0

    def serve_forever(self) -> None:
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((self.host, self.port))
        server_sock.listen(5)
        _log(f"\n{'='*60}")
        _log(f"🏁 Local Competition Server")
        _log(f"  Listening on {self.host}:{self.port}")
        _log(f"  Waiting for 2 players to connect...")
        _log(f"  To start your bot:  python main.py <playerId> {self.host} {self.port}")
        _log(f"  Seed: {self.seed}")
        _log(f"{'='*60}\n")

        while True:
            try:
                conn1, addr1 = server_sock.accept()
                _log(f"  Player 1 connected from {addr1}")
                conn2, addr2 = server_sock.accept()
                _log(f"  Player 2 connected from {addr2}")

                self.match_counter += 1
                match_id = f"match_local_{self.match_counter}"
                match_seed = self.seed + self.match_counter

                runner = MatchRunner(conn1, addr1, conn2, addr2, match_id, match_seed)
                thread = threading.Thread(target=runner.run, daemon=True)
                thread.start()

                _log(f"\n  Waiting for next match...\n")

            except KeyboardInterrupt:
                _log("\n  Server shutting down.")
                break
            except Exception as exc:
                _log(f"  [ERROR] {exc}")
                continue

        server_sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local 荔枝争运战 Server")
    parser.add_argument("--port", type=int, default=SERVER_PORT, help="Port to listen on")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    args = parser.parse_args()

    server = GameServer(port=args.port, seed=args.seed)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
