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
    "iceBoxUnusedLowFreshnessFrames",
    "horseUnusedWhileMovingFrames",
    "intelUnusedBeforeGateFrames",
]

WARNING_RULES = [
    ("idleEmptyCount", 1.0, "存在 IDLE 空动作：优先检查无路径、状态误判、目标为空、异常兜底。"),
    ("highValueAbstainCount", 1.0, "高价值窗口弃权偏多：优先检查 WindowPolicy 价值判断和卡牌资源使用。"),
    ("rejectedActionCount", 1.0, "无效动作偏多：优先检查 LegalAction / 反馈学习 / 冷却抑制。"),
    ("iceBoxUnusedLowFreshnessFrames", 3.0, "低鲜度持有 ICE_BOX 未用：优先检查 ResourceManager 的保鲜触发。"),
    ("horseUnusedWhileMovingFrames", 3.0, "停顿可行动时持有马未用：优先检查马 buff 冲突和出发前使用时机。"),
    ("intelUnusedBeforeGateFrames", 3.0, "90 分后持有 INTEL 未用于宫门/关键点：优先检查探路目标选择。"),
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
    return f"{metric:<34} A={a:8.2f}  B={b:8.2f}  ΔA-B={delta:+8.2f}"


def side_warnings(rows: list[dict[str, str]], side: str) -> list[str]:
    warnings: list[str] = []
    for metric, threshold, message in WARNING_RULES:
        value = avg(rows, side, metric)
        if value >= threshold:
            warnings.append(f"{metric}={value:.2f}：{message}")
    task = avg(rows, side, "taskScore")
    fresh = avg(rows, side, "freshness")
    if task < 90:
        warnings.append(f"taskScore={task:.1f}：任务分未稳定过保底线，优先检查任务选择/绕路收益。")
    elif task < 120:
        warnings.append(f"taskScore={task:.1f}：任务分偏低，可能送太早或任务 EV 太保守。")
    if fresh < 75:
        warnings.append(f"freshness={fresh:.1f}：鲜度明显偏低，优先检查送达锁、冰鉴和绕路。")
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
    for warning in side_warnings(rows, "a") or ["无明显行为审计告警。"]:
        print(f"  - {warning}")
    print("B warnings:")
    for warning in side_warnings(rows, "b") or ["无明显行为审计告警。"]:
        print(f"  - {warning}")
    print("-" * 78)
    print("Priority hints:")
    if avg(rows, "a", "idleEmptyCount") > avg(rows, "b", "idleEmptyCount") + 1:
        print("  - A 的 IDLE 空动作更多，先查 A 的目标生成/路径/状态判断。")
    if avg(rows, "b", "idleEmptyCount") > avg(rows, "a", "idleEmptyCount") + 1:
        print("  - B 的 IDLE 空动作更多，先查 B 的目标生成/路径/状态判断。")
    if avg(rows, "a", "highValueAbstainCount") > avg(rows, "b", "highValueAbstainCount") + 1:
        print("  - A 高价值弃权更多，先调 A 的窗口 EV。")
    if avg(rows, "b", "highValueAbstainCount") > avg(rows, "a", "highValueAbstainCount") + 1:
        print("  - B 高价值弃权更多，先调 B 的窗口 EV。")
    if avg(rows, "a", "taskScore") + 20 < avg(rows, "b", "taskScore"):
        print("  - A 任务分显著低于 B，先查 A 的任务 EV / detour 阈值。")
    if avg(rows, "b", "taskScore") + 20 < avg(rows, "a", "taskScore"):
        print("  - B 任务分显著低于 A，先查 B 的任务 EV / detour 阈值。")
    if avg(rows, "a", "freshness") + 8 < avg(rows, "b", "freshness"):
        print("  - A 鲜度显著低于 B，先查 A 的送达锁和资源使用。")
    if avg(rows, "b", "freshness") + 8 < avg(rows, "a", "freshness"):
        print("  - B 鲜度显著低于 A，先查 B 的送达锁和资源使用。")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose audited tournament CSV")
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args(argv)
    print_report(load_rows(args.csv_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
