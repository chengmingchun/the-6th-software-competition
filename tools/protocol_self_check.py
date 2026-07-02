#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lizhi_agent.protocol import _json_body  # noqa: E402


def frame(payload: dict) -> bytes:
    body = _json_body(payload)
    return f"{len(body):05d}".encode("ascii") + body


def dump(name: str, payload: dict) -> None:
    body = _json_body(payload)
    raw = frame(payload)
    print(f"===== {name} =====")
    print(f"bodyBytes={len(body)} frameBytes={len(raw)} prefix={raw[:5].decode('ascii')}")
    print(body.decode("utf-8"))
    print(raw.decode("utf-8"))
    print()


def main() -> int:
    player_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2779
    match_id = sys.argv[2] if len(sys.argv) > 2 else "match_001"
    round_no = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    player_name = sys.argv[4] if len(sys.argv) > 4 else "lizhi-python-baseline"
    version = sys.argv[5] if len(sys.argv) > 5 else "1.0"

    registration = {
        "msg_name": "registration",
        "msg_data": {
            "playerId": player_id,
            "playerName": player_name,
            "version": version,
        },
    }
    ready = {
        "msg_name": "ready",
        "msg_data": {
            "matchId": match_id,
            "round": round_no,
            "playerId": player_id,
        },
    }
    action_empty = {
        "msg_name": "action",
        "msg_data": {
            "matchId": match_id,
            "round": round_no,
            "playerId": player_id,
            "actions": [],
        },
    }

    dump("registration", registration)
    dump("ready", ready)
    dump("action_empty", action_empty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
