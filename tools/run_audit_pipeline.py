#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], *, allow_fail: bool = False) -> int:
    print("\n$ " + " ".join(cmd))
    code = subprocess.call(cmd, cwd=str(ROOT))
    if code != 0 and not allow_fail:
        raise SystemExit(code)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run audited tournament, report, and regression gate")
    parser.add_argument("--bot-a", default=".")
    parser.add_argument("--bot-b", default="claude")
    parser.add_argument("--seeds", default="1-20")
    parser.add_argument("--summary-csv", default="logs/audit_root_vs_claude.csv")
    parser.add_argument("--swap-sides", action="store_true", default=True)
    parser.add_argument("--no-swap-sides", dest="swap_sides", action="store_false")
    parser.add_argument("--gate-side", choices=["a", "b", "both"], default="b")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--allow-gate-fail", action="store_true", help="Print gate failure but return success; useful during exploration")
    args = parser.parse_args(argv)

    if not args.skip_tests:
        run([sys.executable, "-m", "unittest", "-v", "tests.test_audit_metrics", "tests.test_audit_report", "tests.test_audit_gate"])

    tournament_cmd = [
        sys.executable,
        "tools/audited_tournament_runner.py",
        "--bot-a", args.bot_a,
        "--bot-b", args.bot_b,
        "--seeds", args.seeds,
        "--summary-csv", args.summary_csv,
    ]
    if args.swap_sides:
        tournament_cmd.append("--swap-sides")
    run(tournament_cmd)

    run([sys.executable, "tools/audit_report.py", args.summary_csv])

    gate_cmd = [sys.executable, "tools/audit_gate.py", args.summary_csv, "--side", args.gate_side]
    if args.strict:
        gate_cmd.append("--strict")
    gate_code = run(gate_cmd, allow_fail=args.allow_gate_fail)
    if gate_code == 0:
        print("\nAudit pipeline PASS")
    else:
        print("\nAudit pipeline found regressions; inspect report above.")
    return 0 if args.allow_gate_fail else gate_code


if __name__ == "__main__":
    raise SystemExit(main())
