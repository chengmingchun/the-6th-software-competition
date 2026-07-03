#!/usr/bin/env python3
"""Compare Baseline vs RoadMaster strategies side by side."""
import sys, os, subprocess

ROOT = r"C:\Users\13052\Documents\Lychee"
CLAUDE = os.path.join(ROOT, "claude")

def run_battle(seed, strategy_desc):
    """Run via subprocess. strategy_desc = 'baseline' or 'roadmaster'."""
    if strategy_desc == "baseline":
        # Use original code: add ROOT to path first
        path_add = ROOT
        extra_path = CLAUDE  # for lizhi_server & run_local_battle
        class_name = "BaselineStrategy"
        strategy_mod_path = ROOT
    else:
        path_add = CLAUDE
        extra_path = ""
        class_name = "RoadMasterStrategy"
        strategy_mod_path = CLAUDE

    code = f'''
import sys, os
sys.stderr = open(os.devnull, "w")
os.environ["LIZHI_DEBUG"] = "0"

# Add paths: strategy comes first
sys.path.insert(0, r"{strategy_mod_path}")
sys.path.insert(0, r"{CLAUDE}")  # for lizhi_server & run_local_battle

from lizhi_server.engine import GameEngine as ServerEngine
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state
from run_local_battle import convert_inquire_for_strategy

# Import the correct strategy class
if "{strategy_desc}" == "baseline":
    sys.path.insert(0, r"{ROOT}")
else:
    sys.path.insert(0, r"{CLAUDE}")
from lizhi_agent.strategy import {class_name} as StrategyClass
sys.path.pop(0)

server = ServerEngine(match_id="m_{seed}", seed={seed}, player1_id="1001", player2_id="1002")
start_data = server.get_start_payload("1001")["msg_data"]
config = StrategyConfig.default()
s1 = StrategyClass("1001", config, DecisionLogger("1001", log_dir="logs"))
s2 = StrategyClass("1002", config, DecisionLogger("1002", log_dir="logs"))
s1.on_start(start_data)
s2.on_start(start_data)
for frame in range(1, 601):
    if server.ended:
        break
    iq1 = convert_inquire_for_strategy(start_data, "1001", server, frame)
    iq2 = convert_inquire_for_strategy(start_data, "1002", server, frame)
    st1 = parse_game_state("1001", start_data, iq1)
    st2 = parse_game_state("1002", start_data, iq2)
    try:
        b1 = s1.decide(st1)
        b2 = s2.decide(st2)
    except Exception:
        continue
    server.process_actions(frame, b1.to_actions(), b2.to_actions())
    server._advance_buffs()
over = server.get_over_payload()
od = over["msg_data"]
p1, p2 = od["players"][0], od["players"][1]
avg = (p1["totalScore"] + p2["totalScore"]) // 2
print(f"RESULT {{avg}} {{p1['totalScore']}} {{p2['totalScore']}}")
'''
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT"):
            parts = line.strip().split()
            return int(parts[1]), int(parts[2]), int(parts[3])
    if r.stderr:
        for err_line in r.stderr.splitlines()[:5]:
            print(f"  [{strategy_desc} seed={seed}] {err_line}")
    return None, None, None

seeds = [1, 42, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
bl_all, rm_all = [], []

for seed in seeds:
    ba, b1, b2 = run_battle(seed, "baseline")
    ra, r1, r2 = run_battle(seed, "roadmaster")
    if ba is not None:
        bl_all.append(ba)
    if ra is not None:
        rm_all.append(ra)
    print(f"seed={seed:4d}:  Baseline={ba:>3d} ({b1:>3d},{b2:>3d})  RoadMaster={ra:>3d} ({r1:>3d},{r2:>3d})  delta={ra-ba:+>3d}")

print()
print(f"== Summary over {len(bl_all)} seeds ==")
print(f"Baseline  avg: {sum(bl_all)//len(bl_all):3d}  range=[{min(bl_all)},{max(bl_all)}]")
print(f"RoadMaster avg: {sum(rm_all)//len(rm_all):3d}  range=[{min(rm_all)},{max(rm_all)}]")
if bl_all and rm_all:
    print(f"Improvement: +{sum(rm_all)//len(rm_all) - sum(bl_all)//len(bl_all):3d} pts average")
