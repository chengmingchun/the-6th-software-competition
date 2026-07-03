"""TCP server that runs a full match of 一骑红尘：荔枝争运战.

Usage:
    python -m lizhi_server.run_server [--port PORT]
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import config as C
from .engine import GameEngine


SERVER_PORT = 30000
FRAME_INTERVAL_MS = 500  # 500ms per frame (matches competition spec)
MAX_FRAME_TIMEOUT_MS = FRAME_INTERVAL_MS * 2  # must send action within ~2 frames


class FrameCodec:
    """5-digit length prefix + UTF-8 JSON body."""

    PREFIX_SIZE = 5
    MAX_BODY = 99_999

    def __init__(self, sock: socket.socket, name: str = "") -> None:
        self.sock = sock
        self.buffer = bytearray()
        self.name = name

    def send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > self.MAX_BODY:
            raise ValueError(f"Payload too large: {len(body)} bytes")
        frame = f"{len(body):05d}".encode("ascii") + body
        self.sock.sendall(frame)
        _log(f"  [{self.name}] >>> {payload.get('msg_name')} round={payload.get('msg_data', {}).get('round')}")

    def recv(self, timeout: float = 1.0) -> dict[str, Any] | None:
        self.sock.settimeout(timeout)
        try:
            while True:
                msg = self._try_pop()
                if msg is not None:
                    return msg
                chunk = self.sock.recv(8192)
                if not chunk:
                    return None
                self.buffer.extend(chunk)
        except socket.timeout:
            return None
        except (ConnectionResetError, BrokenPipeError, OSError):
            return None

    def _try_pop(self) -> dict[str, Any] | None:
        if len(self.buffer) < self.PREFIX_SIZE:
            return None
        prefix = bytes(self.buffer[:self.PREFIX_SIZE])
        if not prefix.isdigit():
            raise ValueError(f"Invalid prefix: {prefix!r}")
        size = int(prefix)
        if size > self.MAX_BODY:
            raise ValueError(f"Body too large: {size}")
        end = self.PREFIX_SIZE + size
        if len(self.buffer) < end:
            return None
        raw = bytes(self.buffer[self.PREFIX_SIZE:end])
        del self.buffer[:end]
        return json.loads(raw.decode("utf-8"))


class MatchRunner:
    """Runs a single match with two clients."""

    def __init__(self, conn1: socket.socket, addr1: tuple,
                 conn2: socket.socket, addr2: tuple,
                 match_id: str = "match_local_001",
                 seed: int = 42) -> None:
        self.codec1 = FrameCodec(conn1, "P1")
        self.codec2 = FrameCodec(conn2, "P2")
        self.addr1 = addr1
        self.addr2 = addr2
        self.match_id = match_id
        self.seed = seed
        self.player1_id = "1001"
        self.player2_id = "1002"

    def run(self) -> None:
        _log(f"\n{'='*60}")
        _log(f"Match {self.match_id} started (seed={self.seed})")
        _log(f"  Player 1: {self.addr1}")
        _log(f"  Player 2: {self.addr2}")
        _log(f"{'='*60}")

        try:
            # 1. Receive registration from both
            reg1 = self.codec1.recv(5.0)
            reg2 = self.codec2.recv(5.0)

            if reg1 and reg1.get("msg_name") == "registration":
                self.player1_id = str(reg1["msg_data"].get("playerId", "1001"))
            if reg2 and reg2.get("msg_name") == "registration":
                self.player2_id = str(reg2["msg_data"].get("playerId", "1002"))

            _log(f"  Player1 ID: {self.player1_id}, Player2 ID: {self.player2_id}")

            # 2. Create engine
            engine = GameEngine(
                match_id=self.match_id,
                seed=self.seed,
                player1_id=self.player1_id,
                player2_id=self.player2_id,
            )

            # 3. Send start to both
            start1 = engine.get_start_payload(self.player1_id)
            start2 = engine.get_start_payload(self.player2_id)
            self.codec1.send(start1)
            self.codec2.send(start2)

            # 4. Receive ready from both
            ready1 = self.codec1.recv(5.0)
            ready2 = self.codec2.recv(5.0)
            _log(f"  Ready received from both players")

            # 5. Game loop
            for round_no in range(1, C.MAX_FRAMES + 1):
                if engine.ended:
                    break

                start_time = time.time()

                # Send inquire
                inquire = engine.get_inquire_payload(round_no)
                self.codec1.send(inquire)
                self.codec2.send(inquire)

                # Receive actions concurrently with equal deadline
                actions1, actions2 = self._recv_actions_pair()

                # Process
                a1 = actions1.get("msg_data", {}).get("actions", []) if actions1 else []
                a2 = actions2.get("msg_data", {}).get("actions", []) if actions2 else []

                engine.process_actions(round_no, a1, a2)

                # Log every 50 frames
                if round_no % 50 == 0 or round_no == 1:
                    p1 = engine.players[self.player1_id]
                    p2 = engine.players[self.player2_id]
                    _log(f"  Frame {round_no:3d}: P1@{p1.station} score={p1.total_score:3d} "
                         f"P2@{p2.station} score={p2.total_score:3d} "
                         f"phase={engine.phase}")

                # Advance buffs
                engine._advance_buffs()

            # 6. Send over
            over = engine.get_over_payload()
            self.codec1.send(over)
            self.codec2.send(over)

            p1 = engine.players[self.player1_id]
            p2 = engine.players[self.player2_id]
            _log(f"\n{'='*60}")
            _log(f"Match {self.match_id} finished!")
            _log(f"  Player 1 ({p1.team_id}): {p1.total_score} pts (delivered={p1.delivered})")
            _log(f"  Player 2 ({p2.team_id}): {p2.total_score} pts (delivered={p2.delivered})")
            _log(f"  Frames played: {engine.frame}")
            _log(f"{'='*60}\n")
            # Machine-readable result line for tournament runner
            import json as _json
            _result = {
                "seed": self.seed, "matchId": self.match_id,
                "overRound": engine.frame,
                "players": [
                    {"playerId": p1.player_id, "teamId": p1.team_id,
                     "totalScore": p1.total_score, "delivered": p1.delivered,
                     "deliverRound": p1.deliver_round, "freshness": round(p1.freshness, 2),
                     "goodFruit": p1.good_fruit, "badFruit": p1.bad_fruit,
                     "taskScore": p1.task_score, "bountyScore": p1.bounty_score,
                     "penaltyScore": min(20, max(0, p1.illegal_action_count - 5)) + min(30, p1.post_deliver_penalty * 5)},
                    {"playerId": p2.player_id, "teamId": p2.team_id,
                     "totalScore": p2.total_score, "delivered": p2.delivered,
                     "deliverRound": p2.deliver_round, "freshness": round(p2.freshness, 2),
                     "goodFruit": p2.good_fruit, "badFruit": p2.bad_fruit,
                     "taskScore": p2.task_score, "bountyScore": p2.bounty_score,
                     "penaltyScore": min(20, max(0, p2.illegal_action_count - 5)) + min(30, p2.post_deliver_penalty * 5)},
                ],
                "winnerPlayerId": None,
            }
            if p1.total_score > p2.total_score:
                _result["winnerPlayerId"] = p1.player_id
            elif p2.total_score > p1.total_score:
                _result["winnerPlayerId"] = p2.player_id
            _log(f"MATCH_RESULT_JSON {_json.dumps(_result, ensure_ascii=False, separators=(',', ':'))}")

        except Exception as exc:
            _log(f"  [ERROR] Match failed: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                self.codec1.sock.close()
            except Exception:
                pass
            try:
                self.codec2.sock.close()
            except Exception:
                pass

    def _recv_actions_pair(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Receive actions from both players concurrently with the same deadline.

        Both players get the full frame interval timeout.  If a response comes
        early the thread returns immediately; the join waits until the deadline
        or until both have responded.
        """
        deadline = FRAME_INTERVAL_MS / 1000.0
        results: dict[str, dict[str, Any] | None] = {}
        lock = threading.Lock()

        def recv_one(codec: FrameCodec, key: str) -> None:
            result = codec.recv(deadline)
            with lock:
                results[key] = result

        t1 = threading.Thread(target=recv_one, args=(self.codec1, "p1"), daemon=True)
        t2 = threading.Thread(target=recv_one, args=(self.codec2, "p2"), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=deadline + 0.5)
        t2.join(timeout=deadline + 0.5)

        return results.get("p1"), results.get("p2")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
