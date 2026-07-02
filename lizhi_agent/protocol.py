from __future__ import annotations

import json
import socket
import sys
from typing import Any, BinaryIO, TextIO

from lizhi_agent.actions import ActionBundle, wait
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state
from lizhi_agent.strategy import BaselineStrategy


class ProtocolError(RuntimeError):
    pass


class FrameCodec:
    """Official 5-digit length-prefixed UTF-8 JSON codec."""

    @staticmethod
    def encode(payload: dict[str, Any]) -> bytes:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > 99999:
            raise ProtocolError(f"message body too large: {len(body)} bytes")
        return f"{len(body):05d}".encode("ascii") + body

    @staticmethod
    def read(stream: BinaryIO) -> dict[str, Any] | None:
        prefix = FrameCodec._read_exact(stream, 5)
        if prefix is None:
            return None
        if not prefix.isdigit():
            raise ProtocolError(f"bad length prefix: {prefix!r}")
        length = int(prefix.decode("ascii"))
        body = FrameCodec._read_exact(stream, length)
        if body is None:
            raise ProtocolError("connection closed while reading frame body")
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def write(stream: BinaryIO, payload: dict[str, Any]) -> None:
        stream.write(FrameCodec.encode(payload))
        stream.flush()

    @staticmethod
    def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = stream.read(remaining)
            if chunk == b"" or chunk is None:
                return None if not chunks else b"".join(chunks)
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class JsonLineClient:
    """Competition client.

    Socket mode implements the official protocol:
    registration -> start -> ready -> inquire/action -> over.

    Stdio mode remains JSON-lines for local unit/debug runs only.
    """

    def __init__(self, player_id: str, strategy: BaselineStrategy, logger: DecisionLogger, player_name: str = "lizhi-baseline") -> None:
        self.player_id = str(player_id)
        self.strategy = strategy
        self.logger = logger
        self.player_name = player_name
        self.match_id: str | None = None
        self.start_data: dict[str, Any] | None = None
        self.ready_round: int = 1

    def run_stdio(self) -> int:
        return self._run_stdio_loop(sys.stdin, sys.stdout)

    def run_socket(self, host: str, port: int) -> int:
        self.logger.info("connect", host=host, port=port)
        with socket.create_connection((host, port), timeout=15) as sock:
            sock.settimeout(None)
            reader = sock.makefile("rb")
            writer = sock.makefile("wb")
            self._send_registration(writer)
            return self._run_frame_loop(reader, writer)

    def _run_frame_loop(self, reader: BinaryIO, writer: BinaryIO) -> int:
        while True:
            try:
                payload = FrameCodec.read(reader)
            except Exception as exc:
                self.logger.info("frame_read_error", error=repr(exc))
                return 1
            if payload is None:
                self.logger.info("eof")
                return 0

            msg_name = payload.get("msg_name")
            msg_data = payload.get("msg_data", {})
            self.logger.info("recv", msg_name=msg_name, round=msg_data.get("round") if isinstance(msg_data, dict) else None)

            try:
                if msg_name == "start":
                    self._handle_start(msg_data, writer)
                elif msg_name == "inquire":
                    self._handle_inquire(msg_data, writer)
                elif msg_name == "over":
                    self.logger.info("over", data=msg_data)
                    return 0
                elif msg_name == "error":
                    self.logger.info("server_error", data=msg_data)
                else:
                    self.logger.info("unknown_message", payload=payload)
            except Exception as exc:
                self.logger.info("handle_error", msg_name=msg_name, error=repr(exc))
                if msg_name == "inquire" and isinstance(msg_data, dict):
                    self._send_action(writer, int(msg_data.get("round", 1)), wait("handle_error"))

    def _run_stdio_loop(self, reader: TextIO, writer: TextIO) -> int:
        """Local debug mode: one JSON object per line, no length prefix."""
        for raw_line in reader:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                msg_name = payload.get("msg_name")
                msg_data = payload.get("msg_data", payload)
                if msg_name == "start":
                    self._store_start(msg_data)
                    writer.write(json.dumps(self._ready_message(), ensure_ascii=False) + "\n")
                    writer.flush()
                elif msg_name == "inquire" or msg_name is None:
                    state = parse_game_state(self.player_id, payload, self.start_data)
                    action = self.strategy.decide(state)
                    round_no = int(msg_data.get("round", state.frame)) if isinstance(msg_data, dict) else state.frame
                    writer.write(json.dumps(self._action_message(round_no, action), ensure_ascii=False) + "\n")
                    writer.flush()
                elif msg_name == "over":
                    break
            except Exception as exc:
                self.logger.info("stdio_error", error=repr(exc), line=line[:300])
        self.logger.close()
        return 0

    def _send_registration(self, writer: BinaryIO) -> None:
        message = {
            "msg_name": "registration",
            "msg_data": {
                "playerId": self._player_id_value(),
                "playerName": self.player_name,
                "version": "1.0",
            },
        }
        FrameCodec.write(writer, message)
        self.logger.info("send_registration", playerId=self.player_id)

    def _handle_start(self, msg_data: dict[str, Any], writer: BinaryIO) -> None:
        self._store_start(msg_data)
        FrameCodec.write(writer, self._ready_message())
        self.logger.info("send_ready", matchId=self.match_id, round=self.ready_round)

    def _handle_inquire(self, msg_data: dict[str, Any], writer: BinaryIO) -> None:
        round_no = int(msg_data.get("round", 1))
        state = parse_game_state(self.player_id, {"msg_name": "inquire", "msg_data": msg_data}, self.start_data)
        action = self.strategy.decide(state)
        self._send_action(writer, round_no, action)

    def _send_action(self, writer: BinaryIO, round_no: int, action: ActionBundle) -> None:
        message = self._action_message(round_no, action)
        FrameCodec.write(writer, message)
        self.logger.info("send_action", round=round_no, actions=message["msg_data"]["actions"])

    def _store_start(self, msg_data: dict[str, Any]) -> None:
        self.start_data = msg_data
        self.match_id = msg_data.get("matchId")
        self.ready_round = int(msg_data.get("round", 1))

    def _ready_message(self) -> dict[str, Any]:
        if not self.match_id:
            raise ProtocolError("missing matchId before ready")
        return {
            "msg_name": "ready",
            "msg_data": {
                "matchId": self.match_id,
                "round": self.ready_round,
                "playerId": self._player_id_value(),
            },
        }

    def _action_message(self, round_no: int, action: ActionBundle) -> dict[str, Any]:
        if not self.match_id:
            # Local debug fallback.
            self.match_id = "local_match"
        return {
            "msg_name": "action",
            "msg_data": {
                "matchId": self.match_id,
                "round": round_no,
                "playerId": self._player_id_value(),
                "actions": action.to_actions(),
            },
        }

    def _player_id_value(self) -> int | str:
        try:
            return int(self.player_id)
        except ValueError:
            return self.player_id
