from __future__ import annotations

import json
import socket
import sys
from dataclasses import dataclass, field
from typing import Any, BinaryIO, TextIO

from lizhi_agent.actions import ActionBundle, wait
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state
from lizhi_agent.strategy import BaselineStrategy


def _player_id_value(player_id: str) -> int | str:
    try:
        return int(player_id)
    except ValueError:
        return player_id


@dataclass
class ProtocolContext:
    """State learned from protocol messages and reused for every frame."""

    player_id: str
    match_id: str | None = None
    start_data: dict[str, Any] = field(default_factory=dict)
    last_round: int = 1


class LengthPrefixedCodec:
    """Official TCP codec: five ASCII digits followed by UTF-8 JSON bytes."""

    PREFIX_SIZE = 5
    MAX_BODY_SIZE = 99_999

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.buffer = bytearray()

    def read_message(self) -> dict[str, Any] | None:
        while True:
            message = self._try_pop_message()
            if message is not None:
                return message
            chunk = self.stream.read(4096)
            if not chunk:
                return None
            self.buffer.extend(chunk)

    def write_message(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > self.MAX_BODY_SIZE:
            raise ValueError(f"payload too large for protocol frame: {len(body)} bytes")
        frame = f"{len(body):05d}".encode("ascii") + body
        self.stream.write(frame)
        self.stream.flush()

    def _try_pop_message(self) -> dict[str, Any] | None:
        if len(self.buffer) < self.PREFIX_SIZE:
            return None
        prefix = bytes(self.buffer[: self.PREFIX_SIZE])
        if not prefix.isdigit():
            raise ValueError(f"invalid length prefix: {prefix!r}")
        size = int(prefix)
        if size > self.MAX_BODY_SIZE:
            raise ValueError(f"declared body too large: {size}")
        end = self.PREFIX_SIZE + size
        if len(self.buffer) < end:
            return None
        raw_body = bytes(self.buffer[self.PREFIX_SIZE : end])
        del self.buffer[:end]
        return json.loads(raw_body.decode("utf-8"))


class CompetitionClient:
    """Client for the official competition protocol."""

    def __init__(self, player_id: str, strategy: BaselineStrategy, logger: DecisionLogger) -> None:
        self.context = ProtocolContext(player_id=player_id)
        self.strategy = strategy
        self.logger = logger

    def run_socket(self, host: str, port: int) -> int:
        self.logger.info("connect", host=host, port=port)
        with socket.create_connection((host, port), timeout=15) as sock:
            sock.settimeout(None)
            reader = sock.makefile("rb")
            writer = sock.makefile("wb")
            codec = LengthPrefixedCodec(_Duplex(reader=reader, writer=writer))
            codec.write_message(self._registration_message())
            return self._run_official_loop(codec)

    def run_stdio(self) -> int:
        """Developer-only JSON-lines loop.

        This path keeps tests and hand-written fixtures easy.  Official matches
        should always pass host and port through start.sh.
        """

        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                response = self._handle_message(payload)
                if response is not None:
                    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
            except Exception as exc:
                self.logger.info("stdio_error", error=repr(exc))
                print(json.dumps({"actions": wait("stdio_error").to_actions()}), flush=True)
        self.logger.close()
        return 0

    def _run_official_loop(self, codec: LengthPrefixedCodec) -> int:
        while True:
            payload = codec.read_message()
            if payload is None:
                self.logger.info("server_closed")
                break
            try:
                response = self._handle_message(payload)
                if response is not None:
                    codec.write_message(response)
            except Exception as exc:
                self.logger.info("message_error", error=repr(exc), payload=str(payload)[:500])
                if self.context.match_id is not None:
                    codec.write_message(self._action_message(wait("exception_fallback")))
        self.logger.close()
        return 0

    def _handle_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        msg_name = str(payload.get("msg_name") or payload.get("type") or "").lower()
        msg_data = payload.get("msg_data") if isinstance(payload.get("msg_data"), dict) else payload

        if msg_name == "start":
            self.context.match_id = str(msg_data.get("matchId", ""))
            self.context.start_data = msg_data
            self.context.last_round = int(msg_data.get("round", 1) or 1)
            self.strategy.on_start(msg_data)
            self.logger.info("start", matchId=self.context.match_id, round=self.context.last_round)
            return self._ready_message()

        if msg_name == "inquire" or "round" in msg_data:
            state = parse_game_state(self.context.player_id, self.context.start_data, msg_data)
            self.context.last_round = state.frame
            bundle = self.strategy.decide(state)
            return self._action_message(bundle)

        if msg_name == "over":
            self.logger.info("over", result=msg_data)
            return None

        if msg_name == "error":
            self.logger.info("server_error", error=msg_data)
            return None

        self.logger.info("ignored_message", msgName=msg_name, keys=list(payload.keys()))
        return None

    def _registration_message(self) -> dict[str, Any]:
        return {
            "msg_name": "registration",
            "msg_data": {
                "playerId": _player_id_value(self.context.player_id),
                "playerName": "lizhi-python-baseline",
                "version": "1.0.0",
            },
        }

    def _ready_message(self) -> dict[str, Any]:
        return {
            "msg_name": "ready",
            "msg_data": {
                "matchId": self.context.match_id,
                "round": self.context.last_round or 1,
                "playerId": _player_id_value(self.context.player_id),
            },
        }

    def _action_message(self, bundle: ActionBundle) -> dict[str, Any]:
        return {
            "msg_name": "action",
            "msg_data": {
                "matchId": self.context.match_id,
                "round": self.context.last_round,
                "playerId": _player_id_value(self.context.player_id),
                "actions": bundle.to_actions(),
            },
        }


class _Duplex:
    """Expose one read/write object for LengthPrefixedCodec."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self.reader = reader
        self.writer = writer

    def read(self, size: int) -> bytes:
        return self.reader.read(size)

    def write(self, data: bytes) -> int:
        return self.writer.write(data)

    def flush(self) -> None:
        self.writer.flush()
