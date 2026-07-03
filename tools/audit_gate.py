#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.audit_report import avg, load_rows, pct


@dataclass(frozen=True)
class GateRule:
    side: str
    metric: str
    op: str
    threshold: float
    message: str

    def check(self, rows: list[dict[str, str]]) -> tuple[bool, float]:
        if self.metric == "deliveredPct":
            value = pct(rows, self.side, "delivered")
        else:
            value = avg(rows, self.side, self.metric)
        if self.op == "<=":
            return value <= self.threshold, value
        if self.op == ">=":
            return value >= self.threshold, value
        raise ValueError(f"unsupported op: {self.op}")


def default_rules(side: str, *, strict: bool = False) -> list[GateRule]:
    # Conservative defaults: catch clear regressions without overfitting early experiments.
    idle_limit = 0.5 if strict else 2.0
    rejected_limit = 0.5 if strict else 2.0
    high_value_abstain_limit = 1.0 if strict else 4.0
    ice_unused_limit = 2.0 if strict else 8.0
    horse_unused_limit = 3.0 if strict else 10.0
    intel_unused_limit = 3.0 if strict else 10.0
    min_deliver = 100.0 if strict else 95.0
    return [
        GateRule(side, "deliveredPct", ">=", min_deliver, "送达率不足"),
        GateRule(side, "idleEmptyCount", "<=", idle_limit, "IDLE 空动作过多"),
        GateRule(side, "rejectedActionCount", "<=", rejected_limit, "无效动作过多"),
        GateRule(side, "highValueAbstainCount", "<=", high_value_abstain_limit, "高价值窗口弃权过多"),
        GateRule(side, "iceBoxUnusedLowFreshnessFrames", "<=", ice_unused_limit, "低鲜度持有 ICE_BOX 未用过多"),
        GateRule(side, "horseUnusedWhileMovingFrames", "<=", horse_unused_limit, "移动中持有马未用过多"),
        GateRule(side, "intelUnusedBeforeGateFrames", "<=", intel_unused_limit, "90 分后 INTEL 未用于关键点过多"),
    ]


def parse_custom_rule(raw: str) -> GateRule:
    # Format: a.metric<=threshold:message  or b.metric>=threshold:message
    message = "自定义门禁失败"
    if ":" in raw:
        raw, message = raw.split(":", 1)
    if raw.startswith("a."):
        side = "a"
        expr = raw[2:]
    elif raw.startswith("b."):
        side = "b"
        expr = raw[2:]
    else:
        raise ValueError("rule must start with a. or b.")
    if "<=" in expr:
        metric, threshold = expr.split("<=", 1)
        op = "<="
    elif ">=" in expr:
        metric, threshold = expr.split(">=", 1)
        op = ">="
    else:
        raise ValueError("rule must contain <= or >=")
    return GateRule(side, metric.strip(), op, float(threshold.strip()), message.strip())


def run_gate(rows: list[dict[str, str]], rules: list[GateRule]) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    for rule in rules:
        passed, value = rule.check(rows)
        status = "PASS" if passed else "FAIL"
        lines.append(f"[{status}] {rule.side}.{rule.metric} {rule.op} {rule.threshold:g} | actual={value:.2f} | {rule.message}")
        if not passed:
            ok = False
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail fast on audited tournament regressions")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--side", choices=["a", "b", "both"], default="b", help="Which side to gate; default gates bot B because A is often baseline and B is candidate")
    parser.add_argument("--strict", action="store_true", help="Use stricter thresholds for near-final candidates")
    parser.add_argument("--rule", action="append", default=[], help="Custom rule, e.g. b.highValueAbstainCount<=2:window too passive")
    args = parser.parse_args(argv)

    rows = load_rows(args.csv_path)
    sides = ["a", "b"] if args.side == "both" else [args.side]
    rules: list[GateRule] = []
    for side in sides:
        rules.extend(default_rules(side, strict=args.strict))
    for raw in args.rule:
        rules.append(parse_custom_rule(raw))

    ok, lines = run_gate(rows, rules)
    print("Audit gate")
    for line in lines:
        print(line)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
