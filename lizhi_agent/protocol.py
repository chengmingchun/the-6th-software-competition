from __future__ import annotations

import json
import socket
import sys
from typing import TextIO

from lizhi_agent.actions import ActionBundle, wait
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state
from lizhi_agent.strategy import BaselineStrategy


class JsonLineClient:
    """JSON-lines client.

    The official communication document may define a different envelope. This
    class is the only place that should change when exact protocol fields are
    known.
    """

    def __init__(self, player_id: str, strategy: BaselineStrategy, logger: DecisionLogger) -> None:
        self.player_id = player_id
        self.strategy = strategy
        self.logger = logger

    def run_stdio(self) -> int:
        return self._run_loop(sys.stdin, sys.stdout)

    def run_socket(self, host: str, port: int) -> int:
        self.logger.info("connect", host=host, port=port)
        with socket.create_connection((host, port), timeout=15) as sock:
            sock.settimeout(None)
            reader = sock.makefile("r", encoding="utf-8", newline="\n")
            writer = sock.makefile("w", encoding="utf-8", newline="\n")
            return self._run_loop(reader, writer)

    def _run_loop(self, reader: TextIO, writer: TextIO) -> int:
        for raw_line in reader:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                self.logger.info("bad_json", error=str(exc), line=line[:300])
                self._send(writer, wait("bad_json"))
                continue

            try:
                # Some platforms send handshake messages. Reply with a harmless
                # registration/ready payload if obvious, otherwise treat it as state.
                if self._handle_control_message(payload, writer):
                    continue

                state = parse_game_state(self.player_id, payload)
                action = self.strategy.decide(state)
                self._send(writer, action)
            except Exception as exc:  # keep client alive
                self.logger.info("decision_error", error=repr(exc))
                self._send(writer, wait("decision_error"))
        self.logger.info("eof")
        self.logger.close()
        return 0

    def _handle_control_message(self, payload: dict, writer: TextIO) -> bool:
        msg_type = str(payload.get("type") or payload.get("cmd") or payload.get("messageType") or "").upper()
        if msg_type in {"PING", "HELLO", "INIT", "REGISTER"}:
            response = {
                "type": "READY",
                "playerId": self.player_id,
                "client": "lizhi-python-baseline",
            }
            writer.write(json.dumps(response, ensure_ascii=False) + "\n")
            writer.flush()
            self.logger.info("control_reply", msgType=msg_type)
            return True
        return False

    def _send(self, writer: TextIO, action: ActionBundle) -> None:
        payload = self._wrap_action(action)
        writer.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        writer.flush()

    def _wrap_action(self, action: ActionBundle) -> dict:
        return {
            "playerId": self.player_id,
            "actions": action.to_dict(),
        }
