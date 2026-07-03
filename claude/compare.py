#!/usr/bin/env python3
"""Compare Baseline vs RoadMaster by calling the two runner scripts."""
import subprocess, sys

ROOT = "C:/Users/13052/Documents/Lychee/claude"
seeds = [1, 42, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
bl_scores, rm_scores = [], []

for s in seeds:
    r1 = subprocess.run([sys.executable, ROOT + "/_baseline_runner.py", str(s)], capture_output=True, text=True, timeout=120)
    r2 = subprocess.run([sys.executable, ROOT + "/_roadmaster_runner.py", str(s)], capture_output=True, text=True, timeout=120)

    ba, b1, b2 = None, None, None
    ra, r1_, r2_ = None, None, None

    for line in r1.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) == 3:
            ba, b1, b2 = int(parts[0]), int(parts[1]), int(parts[2])
    for line in r2.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) == 3:
            ra, r1_, r2_ = int(parts[0]), int(parts[1]), int(parts[2])

    if ba is not None: bl_scores.append(ba)
    if ra is not None: rm_scores.append(ra)
    d = (ra or 0) - (ba or 0)
    print(f"seed={s:4d}:  Baseline={ba or 0:>3d} ({b1 or 0:>3d},{b2 or 0:>3d})  RoadMaster={ra or 0:>3d} ({r1_ or 0:>3d},{r2_ or 0:>3d})  delta={d:+>3d}")

print()
if bl_scores and rm_scores:
    ba_avg = sum(bl_scores)//len(bl_scores)
    ra_avg = sum(rm_scores)//len(rm_scores)
    print(f"Baseline  ({len(bl_scores)} seeds): avg={ba_avg:3d}  range=[{min(bl_scores)},{max(bl_scores)}]")
    print(f"RoadMaster({len(rm_scores)} seeds):  avg={ra_avg:3d}  range=[{min(rm_scores)},{max(rm_scores)}]")
    print(f"Improvement: +{ra_avg-ba_avg:3d} pts average")
