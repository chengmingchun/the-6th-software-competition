#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Any

CORE_METRICS = [
    "totalScore",
    "taskScore",
    "freshness",
    "goodFruit",
    "deliverRound",
    "penaltyScore",
    "idleEmptyCount",
    "highValueAbstainCount",
    "abstainCount",
    "useResourceCount",
    "claimTaskCount",
    "claimResourceCount",
    "rejectedActionCount",
    "guardBlockedMoveResultCount",
    "maxGuardBlockedMoveStreak",
    "iceBoxUnusedLowFreshnessFrames",
    "horseUnusedWhileMovingFrames",
    "intelUnusedBeforeGateFrames",
]

WARNING_RULES = [
    ("idleEmptyCount", 1.0, "IDLE empty actions: check no-route fallback, state detection, empty target, and exception fallback."),
    ("highValueAbstainCount", 1.0, "High-value window abstains: check WindowPolicy value scoring and card resource use."),
    ("rejectedActionCount", 1.0, "Rejected actions: check legal-action gate, feedback learning, and reject cooldowns."),
    ("maxGuardBlockedMoveStreak", 3.0, "Repeated MOVE_BLOCKED_BY_GUARD: check guard feedback target binding, cooldown, reroute, and squad support."),
    ("iceBoxUnusedLowFreshnessFrames", 3.0, "ICE_BOX held at low freshness: check freshness protection and delivery-quality resource timing."),
    ("horseUnusedWhileMovingFrames", 3.0, "Horse held while stopped and actionable: check pre-departure speed-resource timing."),
    ("intelUnusedBeforeGateFrames", 3.0, "INTEL held after 90 task score before gate: check blocked-target and chokepoint scouting."),
]


def as_float(value: Any) -> float:
    try:
        if value in (None, "", "None"):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def values(rows: list[dict[str, str]], side: str, metric: str) -> list[float]:
    key = f"{side}_{metric}"
    return [as_float(row.get(key)) for row in rows]


def avg(rows: list[dict[str, str]], side: str, metric: str) -> float:
    vals = values(rows, side, metric)
    return statistics.mean(vals) if vals else 0.0


def pct(rows: list[dict[str, str]], side: str, field: str) -> float:
    key = f"{side}_{field}"
    if not rows:
        return 0.0
    truthy = sum(1 for row in rows if str(row.get(key)).lower() in {"true", "1", "yes"})
    return truthy / len(rows) * 100


def bot_name(rows: list[dict[str, str]], side: str) -> str:
    key = f"{side}_botDir"
    if not rows:
        return side.upper()
    raw = rows[0].get(key) or side.upper()
    return Path(raw).name or raw


def metric_line(rows: list[dict[str, str]], metric: str) -> str:
    a = avg(rows, "a", metric)
    b = avg(rows, "b", metric)
    delta = a - b
    return f"{metric:<34} A={a:8.2f}  B={b:8.2f}  deltaA-B={delta:+8.2f}"


def side_warnings(rows: list[dict[str, str]], side: str) -> list[str]:
    warnings: list[str] = []
    for metric, threshold, message in WARNING_RULES:
        value = avg(rows, side, metric)
        if value >= threshold:
            warnings.append(f"{metric}={value:.2f}: {message}")
    task = avg(rows, side, "taskScore")
    fresh = avg(rows, side, "freshness")
    if task < 90:
        warnings.append(f"taskScore={task:.1f}: task score is below the safety floor; check task selection and detour EV.")
    elif task < 120:
        warnings.append(f"taskScore={task:.1f}: task score is low; delivery may be too early or task EV too conservative.")
    if fresh < 75:
        warnings.append(f"freshness={fresh:.1f}: freshness is low; check delivery lock, ICE_BOX timing, and reroute cost.")
    return warnings


def winner_summary(rows: list[dict[str, str]], a_dir: str, b_dir: str) -> tuple[int, int, int]:
    a_wins = 0
    b_wins = 0
    draws = 0
    for row in rows:
        winner = row.get("winnerBotDir") or ""
        if winner == "DRAW":
            draws += 1
        elif winner.endswith(a_dir) or winner == row.get("a_botDir"):
            a_wins += 1
        elif winner.endswith(b_dir) or winner == row.get("b_botDir"):
            b_wins += 1
    return a_wins, b_wins, draws


def print_report(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("No rows.")
        return
    a_name = bot_name(rows, "a")
    b_name = bot_name(rows, "b")
    a_wins, b_wins, draws = winner_summary(rows, a_name, b_name)
    n = len(rows)
    print("=" * 78)
    print(f"Audit Report: A={a_name} vs B={b_name} | matches={n}")
    print("=" * 78)
    print(f"Win rate: A={a_wins / n * 100:.1f}%  B={b_wins / n * 100:.1f}%  draw={draws / n * 100:.1f}%")
    print(f"Deliver:  A={pct(rows, 'a', 'delivered'):.1f}%  B={pct(rows, 'b', 'delivered'):.1f}%")
    print("-" * 78)
    for metric in CORE_METRICS:
        print(metric_line(rows, metric))
    print("-" * 78)
    print("A warnings:")
    for warning in side_warnings(rows, "a") or ["No obvious behavior audit warnings."]:
        print(f"  - {warning}")
    print("B warnings:")
    for warning in side_warnings(rows, "b") or ["No obvious behavior audit warnings."]:
        print(f"  - {warning}")
    print("-" * 78)
    print("Priority hints:")
    if avg(rows, "a", "idleEmptyCount") > avg(rows, "b", "idleEmptyCount") + 1:
        print("  - A has more IDLE empty actions; inspect A target generation, route planning, and state checks.")
    if avg(rows, "b", "idleEmptyCount") > avg(rows, "a", "idleEmptyCount") + 1:
        print("  - B has more IDLE empty actions; inspect B target generation, route planning, and state checks.")
    if avg(rows, "a", "highValueAbstainCount") > avg(rows, "b", "highValueAbstainCount") + 1:
        print("  - A abstains from high-value windows more often; tune A window EV and counter policy.")
    if avg(rows, "b", "highValueAbstainCount") > avg(rows, "a", "highValueAbstainCount") + 1:
        print("  - B abstains from high-value windows more often; tune B window EV and counter policy.")
    if avg(rows, "a", "taskScore") + 20 < avg(rows, "b", "taskScore"):
        print("  - A task score is much lower than B; inspect A task EV and detour thresholds.")
    if avg(rows, "b", "taskScore") + 20 < avg(rows, "a", "taskScore"):
        print("  - B task score is much lower than A; inspect B task EV and detour thresholds.")
    if avg(rows, "a", "freshness") + 8 < avg(rows, "b", "freshness"):
        print("  - A freshness is much lower than B; inspect A delivery lock and resource timing.")
    if avg(rows, "b", "freshness") + 8 < avg(rows, "a", "freshness"):
        print("  - B freshness is much lower than A; inspect B delivery lock and resource timing.")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose audited tournament CSV")
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args(argv)
    print_report(load_rows(args.csv_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
