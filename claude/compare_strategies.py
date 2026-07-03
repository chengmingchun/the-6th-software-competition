#!/usr/bin/env python3
"""Compare root Baseline self-play vs Claude RoadMaster self-play.

This script intentionally runs each strategy family in a separate Python
subprocess so the two lizhi_agent packages do not collide in sys.modules.
It reports self-play averages over the same seeds. This is a quick stability
comparison, not a true head-to-head match.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parent
ROOT_DIR = CLAUDE_DIR.parent
DEFAULT_SEEDS = [1, 42, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]


def parse_seeds(seed_text: str | None) -> list[int]:
    if not seed_text:
        return DEFAULT_SEEDS
    seed_text = seed_text.strip()
    if "-" in seed_text and "," not in seed_text:
        start, end = seed_text.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(part.strip()) for part in seed_text.split(",") if part.strip()]


def run_family(seed: int, package_dir: Path, strategy_class: str) -> tuple[int, int, int] | None:
    code = f'''
from __future__ import annotations
import os
import sys

os.environ["LIZHI_DEBUG"] = "0"
sys.stderr = open(os.devnull, "w")
sys.path.insert(0, r"{package_dir}")

from lizhi_server.engine import GameEngine as ServerEngine
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state
from lizhi_agent.strategy import {strategy_class} as StrategyClass
from run_local_battle import convert_inquire_for_strategy

server = ServerEngine(match_id="cmp_{seed}", seed={seed}, player1_id="1001", player2_id="1002")
start_data = server.get_start_payload("1001")["msg_data"]
config = StrategyConfig.default()
s1 = StrategyClass("1001", config, DecisionLogger("1001", log_dir=os.devnull))
s2 = StrategyClass("1002", config, DecisionLogger("1002", log_dir=os.devnull))
s1.on_start(start_data)
s2.on_start(start_data)

for frame in range(1, 601):
    if server.ended:
        break
    iq1 = convert_inquire_for_strategy(start_data, "1001", server, frame)
    iq2 = convert_inquire_for_strategy(start_data, "1002", server, frame)
    st1 = parse_game_state("1001", start_data, iq1)
    st2 = parse_game_state("1002", start_data, iq2)
    b1 = s1.decide(st1)
    b2 = s2.decide(st2)
    server.process_actions(frame, b1.to_actions(), b2.to_actions())
    server._advance_buffs()

over = server.get_over_payload()["msg_data"]
p1, p2 = over["players"][0], over["players"][1]
avg = (p1["totalScore"] + p2["totalScore"]) // 2
print(f"RESULT {{avg}} {{p1['totalScore']}} {{p2['totalScore']}}")
'''
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            _, avg, p1, p2 = line.split()
            return int(avg), int(p1), int(p2)
    print(f"[WARN] {strategy_class} seed={seed} produced no RESULT")
    if proc.stderr:
        print(proc.stderr.splitlines()[0])
    return None


def fmt(value: int | None) -> str:
    return f"{value:>4d}" if value is not None else "None"


def summarize(label: str, scores: list[int]) -> None:
    if not scores:
        print(f"{label}: no successful runs")
        return
    print(
        f"{label:<12} n={len(scores):>3d} "
        f"avg={statistics.mean(scores):6.1f} "
        f"median={statistics.median(scores):6.1f} "
        f"range=[{min(scores)},{max(scores)}]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare root Baseline and Claude RoadMaster self-play")
    parser.add_argument("--seeds", default=None, help="Seed list like 1,2,42 or range like 1-20")
    args = parser.parse_args(argv)

    seeds = parse_seeds(args.seeds)
    baseline_scores: list[int] = []
    roadmaster_scores: list[int] = []

    print("Baseline self-play vs RoadMaster self-play")
    print(f"Root:   {ROOT_DIR}")
    print(f"Claude: {CLAUDE_DIR}")
    print()

    for seed in seeds:
        baseline = run_family(seed, ROOT_DIR, "BaselineStrategy")
        roadmaster = run_family(seed, CLAUDE_DIR, "RoadMasterStrategy")
        if baseline is not None:
            baseline_scores.append(baseline[0])
        if roadmaster is not None:
            roadmaster_scores.append(roadmaster[0])
        b_avg, b1, b2 = baseline if baseline else (None, None, None)
        r_avg, r1, r2 = roadmaster if roadmaster else (None, None, None)
        delta = None if b_avg is None or r_avg is None else r_avg - b_avg
        delta_text = f"{delta:+d}" if delta is not None else "None"
        print(
            f"seed={seed:>4d}: "
            f"Baseline={fmt(b_avg)} ({fmt(b1)},{fmt(b2)})  "
            f"RoadMaster={fmt(r_avg)} ({fmt(r1)},{fmt(r2)})  "
            f"delta={delta_text}"
        )

    print()
    summarize("Baseline", baseline_scores)
    summarize("RoadMaster", roadmaster_scores)
    if baseline_scores and roadmaster_scores:
        print(f"Average delta: {statistics.mean(roadmaster_scores) - statistics.mean(baseline_scores):+.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
