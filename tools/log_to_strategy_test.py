#!/usr/bin/env python3
"""Generate replayable strategy tests from Lizhi client logs.

The preferred input is the machine-readable marker emitted by the client:

    @@LIZHI_FIXTURE@@{"round":57,"startData":...,"inquireData":...,"expectedActions":[...]}

It also supports JSON log lines produced by LIZHI_LOG_STYLE=json where
`event == "fixture_frame"`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MARKER = "@@LIZHI_FIXTURE@@"


@dataclass(frozen=True)
class FrameFixture:
    round: int
    player_id: str
    match_id: str | None
    start_data: dict[str, Any]
    inquire_data: dict[str, Any]
    expected_actions: list[dict[str, Any]]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _loads_candidate(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _record_to_fixture(record: dict[str, Any]) -> FrameFixture | None:
    event = record.get("event")
    payload = record
    if event == "fixture_frame":
        payload = record
    elif "startData" not in record or "inquireData" not in record:
        return None

    start_data = payload.get("startData")
    inquire_data = payload.get("inquireData")
    if not isinstance(start_data, dict) or not isinstance(inquire_data, dict):
        return None

    round_no = _as_int(payload.get("round") or inquire_data.get("round"))
    if round_no is None:
        return None
    player_id = str(payload.get("playerId") or payload.get("player_id") or "")
    if not player_id:
        # Fall back to the first player in the packet when possible.
        players = inquire_data.get("players") if isinstance(inquire_data.get("players"), list) else []
        if players and isinstance(players[0], dict):
            player_id = str(players[0].get("playerId", ""))
    if not player_id:
        return None

    expected = payload.get("expectedActions")
    if not isinstance(expected, list):
        expected = []
    expected_actions = [item for item in expected if isinstance(item, dict)]
    return FrameFixture(
        round=round_no,
        player_id=player_id,
        match_id=str(payload.get("matchId")) if payload.get("matchId") is not None else None,
        start_data=start_data,
        inquire_data=inquire_data,
        expected_actions=expected_actions,
    )


def load_fixtures(log_path: Path) -> list[FrameFixture]:
    fixtures: list[FrameFixture] = []
    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if MARKER in line:
            candidate = _loads_candidate(line.split(MARKER, 1)[1].strip())
            if candidate is not None:
                fixture = _record_to_fixture(candidate)
                if fixture is not None:
                    fixtures.append(fixture)
            continue
        if line.startswith("{"):
            record = _loads_candidate(line)
            if record is not None and record.get("event") == "fixture_frame":
                fixture = _record_to_fixture(record)
                if fixture is not None:
                    fixtures.append(fixture)
    return fixtures


def select_fixtures(fixtures: list[FrameFixture], frame: int | None, start: int | None, end: int | None) -> list[FrameFixture]:
    if frame is not None:
        selected = [fixture for fixture in fixtures if fixture.round == frame]
    else:
        if start is None or end is None:
            raise SystemExit("Either --frame or both --from-frame/--to-frame are required.")
        lo, hi = sorted((start, end))
        selected = [fixture for fixture in fixtures if lo <= fixture.round <= hi]
    selected.sort(key=lambda item: item.round)
    return selected


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_")
    return cleaned or "replay"


def render_test(fixtures: list[FrameFixture], assert_expected: bool, test_name: str) -> str:
    if not fixtures:
        raise SystemExit("No frames selected.")
    payloads = [
        {
            "round": fixture.round,
            "playerId": fixture.player_id,
            "matchId": fixture.match_id,
            "startData": fixture.start_data,
            "inquireData": fixture.inquire_data,
            "expectedActions": fixture.expected_actions,
        }
        for fixture in fixtures
    ]
    payload_json = json.dumps(payloads, ensure_ascii=False, indent=2, sort_keys=True)
    method_name = "test_" + _safe_name(test_name)
    assert_block = """
            if expected:
                self.assertEqual(actions, expected)
""" if assert_expected else """
            # Intentionally no equality assertion. Use this mode when you want
            # to print and inspect the current strategy output after changing code.
"""
    return f'''from __future__ import annotations

import json
import unittest

from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state
from lizhi_agent.strategy import BaselineStrategy


FIXTURES = {payload_json}


class SilentLogger(DecisionLogger):
    def __init__(self) -> None:
        pass

    def info(self, event: str, **fields):
        return None

    def close(self) -> None:
        return None


class GeneratedReplayTest(unittest.TestCase):
    def {method_name}(self) -> None:
        if not FIXTURES:
            self.fail("No fixtures generated")
        strategy = BaselineStrategy(str(FIXTURES[0]["playerId"]), StrategyConfig.default(), SilentLogger())
        strategy.on_start(FIXTURES[0]["startData"])
        for fixture in FIXTURES:
            state = parse_game_state(str(fixture["playerId"]), fixture["startData"], fixture["inquireData"])
            bundle = strategy.decide(state)
            actions = bundle.to_actions()
            expected = fixture.get("expectedActions") or []
            print("FRAME", fixture["round"], "ACTIONS", json.dumps(actions, ensure_ascii=False, separators=(",", ":")))
{assert_block}


if __name__ == "__main__":
    unittest.main()
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert Lizhi debug logs into replayable strategy tests.")
    parser.add_argument("--log", required=True, type=Path, help="Path to .log or .jsonl containing fixture_frame markers.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--frame", type=int, help="Single frame to extract.")
    group.add_argument("--range", nargs=2, type=int, metavar=("FROM", "TO"), help="Inclusive frame range to extract.")
    parser.add_argument("--out", type=Path, help="Output test file. Defaults to tests/generated/test_replay_<frames>.py")
    parser.add_argument("--no-assert", action="store_true", help="Generate a smoke/replay test that prints current actions without asserting logged actions.")
    parser.add_argument("--list", action="store_true", help="List available fixture frames and exit.")
    args = parser.parse_args(argv)

    fixtures = load_fixtures(args.log)
    if args.list:
        for fixture in fixtures:
            print(fixture.round)
        return 0
    if not fixtures:
        raise SystemExit(
            "No fixture frames found. Re-run the client with LIZHI_FIXTURE_LOG=1. "
            "Pretty logs should contain @@LIZHI_FIXTURE@@ markers; JSON logs should contain event=fixture_frame."
        )

    if args.range:
        start, end = args.range
        selected = select_fixtures(fixtures, None, start, end)
        suffix = f"{start}_{end}"
    else:
        selected = select_fixtures(fixtures, args.frame, None, None)
        suffix = str(args.frame)

    if not selected:
        available = ", ".join(str(item.round) for item in fixtures[:50])
        raise SystemExit(f"No matching frame found. Available examples: {available}")

    out = args.out or Path("tests/generated") / f"test_replay_{suffix}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    test_name = f"replay_{suffix}"
    out.write_text(render_test(selected, assert_expected=not args.no_assert, test_name=test_name), encoding="utf-8")
    print(f"Wrote {out} with {len(selected)} frame(s): {selected[0].round}..{selected[-1].round}")
    print(f"Run: python -m unittest {out.as_posix().replace('/', '.')[:-3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
