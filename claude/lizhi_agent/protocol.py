from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, BinaryIO, Protocol

from lizhi_agent.actions import ActionBundle, wait
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state
from lizhi_agent.strategy import FreshnessFirstStrategy as BaselineStrategy

DEFAULT_PLAYER_NAME = "你荔枝一点"


def _player_id_value(player_id: str) -> int | str:
    try:
        return int(player_id)
    except ValueError:
        return player_id


def _json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _frame_bytes(payload: dict[str, Any]) -> bytes:
    body = _json_body(payload)
    if len(body) > LengthPrefixedCodec.MAX_BODY_SIZE:
        raise ValueError(f"payload too large for protocol frame: {len(body)} bytes")
    return f"{len(body):05d}".encode("ascii") + body


def _preview(value: Any, limit: int = 3000) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...<truncated {len(text) - limit} chars>"


def _raw_payload_logging_enabled() -> bool:
    return os.environ.get("LIZHI_RAW_LOG", "1") != "0"


def _fixture_logging_enabled() -> bool:
    return os.environ.get("LIZHI_FIXTURE_LOG", "1") != "0"


def _fixture_log_path(player_id: str) -> str:
    explicit = os.environ.get("LIZHI_FIXTURE_LOG_PATH")
    if explicit:
        return explicit
    log_dir = os.environ.get("LIZHI_LOG_DIR", "logs")
    return os.path.join(log_dir, f"{player_id}.fixtures.jsonl")


@dataclass
class ProtocolContext:
    """State learned from protocol messages and reused for every frame."""

    player_id: str
    match_id: str | None = None
    start_data: dict[str, Any] = field(default_factory=dict)
    last_round: int = 1
    sent_registration: bool = False
    sent_ready: bool = False
    sent_actions: int = 0


class MessageCodec(Protocol):
    def read_message(self) -> dict[str, Any] | None: ...

    def write_message(self, payload: dict[str, Any]) -> None: ...


class LengthPrefixedCodec:
    """Official frame codec for file-like streams.

    Kept for tests and stdio-style harnesses. Official socket matches use
    RawSocketCodec below so every outbound frame is sent via socket.sendall().
    """

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
        frame = _frame_bytes(payload)
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


class RawSocketCodec:
    """Official TCP codec using raw socket recv/sendall.

    This avoids ambiguity from socket.makefile buffering. If the log says
    frame_sent, the bytes were handed to the OS through sendall().
    """

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buffer = bytearray()

    def read_message(self) -> dict[str, Any] | None:
        while True:
            message = self._try_pop_message()
            if message is not None:
                return message
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self.buffer.extend(chunk)

    def write_message(self, payload: dict[str, Any]) -> None:
        self.sock.sendall(_frame_bytes(payload))

    def _try_pop_message(self) -> dict[str, Any] | None:
        if len(self.buffer) < LengthPrefixedCodec.PREFIX_SIZE:
            return None
        prefix = bytes(self.buffer[: LengthPrefixedCodec.PREFIX_SIZE])
        if not prefix.isdigit():
            raise ValueError(f"invalid length prefix: {prefix!r}")
        size = int(prefix)
        if size > LengthPrefixedCodec.MAX_BODY_SIZE:
            raise ValueError(f"declared body too large: {size}")
        end = LengthPrefixedCodec.PREFIX_SIZE + size
        if len(self.buffer) < end:
            return None
        raw_body = bytes(self.buffer[LengthPrefixedCodec.PREFIX_SIZE : end])
        del self.buffer[:end]
        return json.loads(raw_body.decode("utf-8"))


class CompetitionClient:
    """Client for the official competition protocol."""

    def __init__(self, player_id: str, strategy: BaselineStrategy, logger: DecisionLogger) -> None:
        self.context = ProtocolContext(player_id=player_id)
        self.strategy = strategy
        self.logger = logger

    def run_socket(self, host: str, port: int) -> int:
        self.logger.info("connect", host=host, port=port, mode="socket", playerIdValue=_player_id_value(self.context.player_id))
        with socket.create_connection((host, port), timeout=15) as sock:
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            codec = RawSocketCodec(sock)
            registration = self._registration_message()
            self._send_message(codec, registration)
            self.context.sent_registration = True
            self.logger.info("registration_sent", playerId=self.context.player_id)
            return self._run_official_loop(codec)

    def run_stdio(self) -> int:
        """Developer-only JSON-lines loop.

        This path keeps tests and hand-written fixtures easy. Official matches
        should always pass host and port through start.sh.
        """

        self.logger.info("stdio_mode", note="waiting for JSON lines on stdin")
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                self._log_inbound(payload)
                response = self._handle_message(payload)
                if response is not None:
                    self._log_outbound(response)
                    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
            except Exception as exc:
                self.logger.info("stdio_error", error=repr(exc), line=line[:500])
                print(json.dumps({"actions": wait("stdio_error").to_actions()}), flush=True)
        self.logger.close()
        return 0

    def _run_official_loop(self, codec: MessageCodec) -> int:
        while True:
            payload = codec.read_message()
            if payload is None:
                self.logger.info(
                    "server_closed",
                    matchId=self.context.match_id,
                    lastRound=self.context.last_round,
                    sentRegistration=self.context.sent_registration,
                    sentReady=self.context.sent_ready,
                    sentActions=self.context.sent_actions,
                    startReceived=bool(self.context.start_data),
                    note="connection closed by server; if this happens right after ready, inspect ready msg_data and server-side validation",
                )
                break
            try:
                self._log_inbound(payload)
                response = self._handle_message(payload)
                if response is not None:
                    self._send_message(codec, response)
                    self._mark_sent(response)
            except Exception as exc:
                self.logger.info("message_error", error=repr(exc), payloadPreview=_preview(payload, 2000))
                if self.context.match_id is not None:
                    fallback = self._action_message(wait("exception_fallback"))
                    self._send_message(codec, fallback)
                    self._mark_sent(fallback)
        self.logger.close()
        return 0

    def _send_message(self, codec: MessageCodec, payload: dict[str, Any]) -> None:
        self._log_outbound(payload)
        frame = _frame_bytes(payload)
        codec.write_message(payload)
        self.logger.info(
            "frame_sent",
            msgName=payload.get("msg_name"),
            prefix=frame[: LengthPrefixedCodec.PREFIX_SIZE].decode("ascii"),
            bodyBytes=len(frame) - LengthPrefixedCodec.PREFIX_SIZE,
            frameBytes=len(frame),
        )

    def _handle_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        msg_name = str(payload.get("msg_name") or payload.get("type") or "").lower()
        msg_data = payload.get("msg_data") if isinstance(payload.get("msg_data"), dict) else payload

        self.logger.info("handle_message", msgName=msg_name, msgDataKeys=list(msg_data.keys()) if isinstance(msg_data, dict) else None)

        if msg_name == "start":
            self.context.match_id = str(msg_data.get("matchId", ""))
            self.context.start_data = msg_data
            self.context.last_round = int(msg_data.get("round", 1) or 1)
            self._log_start_detail(msg_data)
            self.strategy.on_start(msg_data)
            self.logger.info("start", matchId=self.context.match_id, round=self.context.last_round)
            return self._ready_message()

        if msg_name == "inquire" or "round" in msg_data:
            self._log_inquire_detail(msg_data)
            state = parse_game_state(self.context.player_id, self.context.start_data, msg_data)
            self.context.last_round = state.frame
            bundle = self.strategy.decide(state)
            response = self._action_message(bundle)
            self._log_replay_fixture(msg_data, response)
            return response

        if msg_name == "over":
            self.logger.info("over", result=msg_data, payloadPreview=_preview(payload, 3000))
            return None

        if msg_name == "error":
            self.logger.info("server_error", error=msg_data, payloadPreview=_preview(payload, 3000))
            return None

        self.logger.info("ignored_message", msgName=msg_name, keys=list(payload.keys()), payloadPreview=_preview(payload, 2000))
        return None

    def _mark_sent(self, payload: dict[str, Any]) -> None:
        msg_name = payload.get("msg_name")
        if msg_name == "ready":
            self.context.sent_ready = True
        elif msg_name == "action":
            self.context.sent_actions += 1

    def _log_replay_fixture(self, inquire_data: dict[str, Any], response: dict[str, Any]) -> None:
        if not _fixture_logging_enabled():
            return
        response_data = response.get("msg_data") if isinstance(response.get("msg_data"), dict) else {}
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "fixture_frame",
            "round": inquire_data.get("round"),
            "playerId": self.context.player_id,
            "matchId": self.context.match_id,
            "startData": self.context.start_data,
            "inquireData": inquire_data,
            "expectedActions": response_data.get("actions", []),
        }
        path = _fixture_log_path(self.context.player_id)
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as fixture_file:
                fixture_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
        except Exception as exc:
            self.logger.info("fixture_log_error", error=repr(exc), path=path, round=inquire_data.get("round"))

    def _log_inbound(self, payload: dict[str, Any]) -> None:
        msg_name = payload.get("msg_name") or payload.get("type")
        msg_data = payload.get("msg_data") if isinstance(payload.get("msg_data"), dict) else {}
        fields: dict[str, Any] = {
            "msgName": msg_name,
            "round": msg_data.get("round"),
            "phase": msg_data.get("phase"),
            "payloadKeys": list(payload.keys()),
            "msgDataKeys": list(msg_data.keys()) if isinstance(msg_data, dict) else None,
            "players": len(msg_data.get("players", []) or []) if isinstance(msg_data.get("players"), list) else None,
            "nodes": len(msg_data.get("nodes", []) or []) if isinstance(msg_data.get("nodes"), list) else None,
            "edges": len(msg_data.get("edges", []) or []) if isinstance(msg_data.get("edges"), list) else None,
            "tasks": len(msg_data.get("tasks", []) or []) if isinstance(msg_data.get("tasks"), list) else None,
            "contests": len(msg_data.get("contests", []) or []) if isinstance(msg_data.get("contests"), list) else None,
            "events": len(msg_data.get("events", []) or []) if isinstance(msg_data.get("events"), list) else None,
            "actionResults": len(msg_data.get("actionResults", []) or []) if isinstance(msg_data.get("actionResults"), list) else None,
        }
        if _raw_payload_logging_enabled():
            fields["payloadPreview"] = _preview(payload, 6000)
        self.logger.info("recv_message", **fields)

    def _log_outbound(self, payload: dict[str, Any]) -> None:
        msg_data = payload.get("msg_data") if isinstance(payload.get("msg_data"), dict) else {}
        body = _json_body(payload)
        fields: dict[str, Any] = {
            "msgName": payload.get("msg_name"),
            "round": msg_data.get("round"),
            "bodyBytes": len(body),
            "frameBytes": len(body) + LengthPrefixedCodec.PREFIX_SIZE,
            "matchId": msg_data.get("matchId"),
            "playerId": msg_data.get("playerId"),
            "actions": msg_data.get("actions"),
            "msgData": msg_data,
        }
        if _raw_payload_logging_enabled():
            fields["payloadPreview"] = _preview(payload, 6000)
        self.logger.info("send_message", **fields)

    def _log_start_detail(self, msg_data: dict[str, Any]) -> None:
        map_data = msg_data.get("map") if isinstance(msg_data.get("map"), dict) else {}
        gameplay = map_data.get("gameplay") if isinstance(map_data.get("gameplay"), dict) else {}
        roles = gameplay.get("roles") if isinstance(gameplay.get("roles"), dict) else {}
        self.logger.info(
            "start_detail",
            matchId=msg_data.get("matchId"),
            round=msg_data.get("round"),
            durationRound=msg_data.get("durationRound"),
            players=msg_data.get("players"),
            nodeCount=len(msg_data.get("nodes", []) or []) if isinstance(msg_data.get("nodes"), list) else None,
            edgeCount=len(msg_data.get("edges", []) or []) if isinstance(msg_data.get("edges"), list) else None,
            roleKeys=list(roles.keys()),
            roles=roles,
            gameplayKeys=list(gameplay.keys()),
        )

    def _log_inquire_detail(self, msg_data: dict[str, Any]) -> None:
        players = msg_data.get("players") if isinstance(msg_data.get("players"), list) else []
        me = None
        for player in players:
            if isinstance(player, dict) and str(player.get("playerId")) == str(self.context.player_id):
                me = player
                break
        self.logger.info(
            "inquire_detail",
            round=msg_data.get("round"),
            phase=msg_data.get("phase"),
            myPlayer=me,
            tasks=msg_data.get("tasks") if _raw_payload_logging_enabled() else len(msg_data.get("tasks", []) or []),
            contests=msg_data.get("contests") if _raw_payload_logging_enabled() else len(msg_data.get("contests", []) or []),
            events=msg_data.get("events") if _raw_payload_logging_enabled() else len(msg_data.get("events", []) or []),
            actionResults=msg_data.get("actionResults") if _raw_payload_logging_enabled() else len(msg_data.get("actionResults", []) or []),
        )

    def _registration_message(self) -> dict[str, Any]:
        return {
            "msg_name": "registration",
            "msg_data": {
                "playerId": _player_id_value(self.context.player_id),
                "playerName": os.environ.get("LIZHI_PLAYER_NAME", DEFAULT_PLAYER_NAME),
                "version": os.environ.get("LIZHI_VERSION", "1.0"),
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
