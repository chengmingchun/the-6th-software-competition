#!/usr/bin/env python3
"""Local dual-bot battle: run two baseline strategies against each other.

Usage:
    python run_local_battle.py [--seed SEED]
    python run_local_battle.py --seeds 1-20 [--summary-csv FILE]
    python run_local_battle.py --seed-list 1,2,3,42

With --fast, runs at maximum speed with no frame-by-frame delay.
Logs are written to logs/ for later analysis.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import sys
import time
from pathlib import Path

# Ensure we can import from project root
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lizhi_server.engine import GameEngine as ServerEngine

# Import the baseline strategy
from lizhi_agent.actions import MainActionType, SquadActionType, WindowCard, wait
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state, GameState
from lizhi_agent.strategy import BaselineStrategy


class SilentLogger:
    def info(self, event: str, **fields):
        return None
    def close(self):
        return None
    def __init__(self):
        pass


def convert_inquire_for_strategy(start_data: dict, player_id: str,
                                 server: ServerEngine, round_no: int) -> dict:
    """Convert server engine state to an inquire dict the strategy can parse."""
    p = server.players[player_id]
    p1 = server.players[server.player1_id]
    p2 = server.players[server.player2_id]

    # Build player state
    def player_dict(pid: str, player) -> dict:
        data = {
            "playerId": pid,
            "teamId": player.team_id,
            "state": player.status,
            "currentNodeId": player.station,
            "nextNodeId": player.target_station,
            "routeEdgeId": player.route_edge,
            "routeType": player.route_type,
            "goodFruit": player.good_fruit,
            "frozenGoodFruit": player.frozen_good_fruit,
            "badFruit": player.bad_fruit,
            "freshness": round(player.freshness, 2),
            "taskScore": player.task_score_base,
            "bountyScore": player.bounty_score,
            "totalScore": player.total_score,
            "delivered": player.delivered,
            "verified": player.verified,
            "retired": player.retired,
            "resources": dict(player.resources),
            "squadAvailable": player.squad_available,
            "squadInFlight": player.squad_in_flight,
            "guardActionPoint": player.guard_points,
            "rushTacticUsedCount": player.rush_tactic_used,
        }
        buffs = [{"type": b, "remaining": r} for b, r in player.buffs.items() if r > 0]
        if buffs:
            data["buffs"] = buffs
        # Include currentProcess so strategy can detect busy states
        if player.current_process:
            data["currentProcess"] = player.current_process
        return data

    # Build nodes — include scout markers and process info
    nodes = []
    for nid, sinfo in server.stations.items():
        node = {
            "nodeId": nid,
            "name": sinfo.get("name", ""),
            "nodeType": sinfo.get("nodeType", ""),
            "type": sinfo.get("type", ""),
            "hasObstacle": sinfo.get("hasObstacle", False),
            "canWindow": True,
            "processType": sinfo.get("processType", ""),
            "processRound": sinfo.get("processRound", 0),
        }
        # Resource stock
        stock = server.resource_stock.get(nid, {})
        if stock:
            node["resourceStock"] = dict(stock)
        # Guard
        for opid, op in server.players.items():
            if nid in op.guards and op.guards[nid].defense > 0:
                node["guard"] = {"ownerTeamId": op.guards[nid].owner_team, "defense": op.guards[nid].defense}
                break
        # Scout markers
        markers = server.scout_markers.get(nid, [])
        active_markers = [m for m in markers if m.end_frame >= round_no and not m.used]
        if active_markers:
            node["scouted"] = [
                {"teamId": m.team_id, "remainingTriggers": 1, "endFrame": m.end_frame}
                for m in active_markers
            ]
        nodes.append(node)

    # Build edges
    edges = []
    for e in server.edges:
        edges.append({
            "edgeId": e["id"],
            "fromNodeId": e["from"],
            "toNodeId": e["to"],
            "routeType": e["type"],
            "distance": e["dist"],
            "bidirectional": e.get("bidirectional", True),
        })

    # Build tasks
    tasks = []
    for t in server.tasks:
        if t.active and not t.completed and not t.failed:
            tasks.append({
                "taskId": t.task_id,
                "taskTemplateId": t.template,
                "name": f"Task-{t.template}",
                "nodeId": t.target,
                "routeBucket": "",
                "processType": "CLAIM_TASK",
                "processRound": t.process_frames,
                "score": t.score,
                "refreshRound": t.refresh_frame,
                "expireRound": t.expire_frame,
                "active": t.active,
                "completed": False,
                "failed": False,
                "ownerPlayerId": t.owner_player_id or 0,
                "protectionPlayerId": t.protection_player_id or 0,
            })

    # Contests
    contests = []
    for c in server.contests:
        if c.resolved or c.suppressed:
            continue
        cdata = {
            "contestId": c.contest_id,
            "contestType": c.contest_type,
            "targetNodeId": c.target_node,
            "roundIndex": c.round_index,
            "totalRounds": c.total_rounds,
            "deadlineRound": c.deadline_round,
            "redPoint": c.red_point,
            "bluePoint": c.blue_point,
            "resolved": c.resolved,
        }
        if c.resource_type:
            cdata["resourceType"] = c.resource_type
        if c.task_id:
            cdata["taskId"] = c.task_id
        for pid, pobj in [(server.player1_id, p1), (server.player2_id, p2)]:
            if pobj.team_id == "RED":
                cdata["redPlayerId"] = pid
            else:
                cdata["bluePlayerId"] = pid
        contests.append(cdata)

    # Weather
    weather = {}
    if server.current_weather:
        weather["active"] = [{
            "type": server.current_weather.weather_type,
            "startRound": server.current_weather.start_frame,
            "endRound": server.current_weather.end_frame,
        }]

    # Events
    events = [{"type": e.type, "round": e.round, "payload": e.payload} for e in server.events]

    # Action results
    action_results = [dict(r) for r in server.action_results]

    return {
        "matchId": server.match_id,
        "round": round_no,
        "tick": round_no - 1,
        "phase": server.phase,
        "players": [player_dict(server.player1_id, p1), player_dict(server.player2_id, p2)],
        "nodes": nodes,
        "edges": edges,
        "tasks": tasks,
        "contests": contests,
        "weather": weather,
        "events": events,
        "actionResults": action_results,
        "scorePreview": {"RED": p1.total_score if p1.team_id == "RED" else p2.total_score,
                         "BLUE": p1.total_score if p1.team_id == "BLUE" else p2.total_score},
    }


def run_battle(seed: int = 42, player1_id: str = "1001", player2_id: str = "1002",
               log_dir: str = "logs", fast: bool = True,
               strategy_cls=None) -> dict:
    """Run a full match between two strategies.

    Returns the over payload dict with results.
    """
    if strategy_cls is None:
        strategy_cls = BaselineStrategy

    # Create server engine
    server = ServerEngine(
        match_id=f"battle_{seed}",
        seed=seed,
        player1_id=player1_id,
        player2_id=player2_id,
    )

    # Get start data (same for both players)
    start_data = server.get_start_payload(player1_id)["msg_data"]

    # Create strategies
    config = StrategyConfig.default()
    logger1 = DecisionLogger(player_id=player1_id, log_dir=log_dir)
    logger2 = DecisionLogger(player_id=player2_id, log_dir=log_dir)
    strategy1 = strategy_cls(player_id=player1_id, config=config, logger=logger1)
    strategy2 = strategy_cls(player_id=player2_id, config=config, logger=logger2)

    strategy1.on_start(start_data)
    strategy2.on_start(start_data)

    start_time = time.time()

    # Action counters per player
    action_counts: dict[str, dict[str, int]] = {
        player1_id: {},
        player2_id: {},
    }

    def _update_action_counts(pid: str, actions: list[dict]) -> None:
        cnt = action_counts[pid]
        if not actions:
            cnt["empty_action"] = cnt.get("empty_action", 0) + 1
        for a in actions:
            atype = a.get("action", "UNKNOWN")
            cnt[atype] = cnt.get(atype, 0) + 1
            if atype == "WINDOW_CARD":
                card = a.get("card", "UNKNOWN")
                cnt[f"window_card_{card}"] = cnt.get(f"window_card_{card}", 0) + 1

    def _count_action_results(pid: str) -> None:
        """Tally accepted/rejected from the latest frame's action_results."""
        cnt = action_counts[pid]
        for ar in server.action_results:
            if str(ar.get("playerId")) == str(pid):
                if ar.get("accepted", False):
                    cnt["accepted_action"] = cnt.get("accepted_action", 0) + 1
                else:
                    cnt["rejected_action"] = cnt.get("rejected_action", 0) + 1

    # Game loop
    for frame in range(1, 601):
        if server.ended:
            break

        # Convert server state to inquire dict for each player
        inquire1 = convert_inquire_for_strategy(start_data, player1_id, server, frame)
        inquire2 = convert_inquire_for_strategy(start_data, player2_id, server, frame)

        # Build GameState objects
        state1 = parse_game_state(player1_id, start_data, inquire1)
        state2 = parse_game_state(player2_id, start_data, inquire2)

        # Get actions
        try:
            bundle1 = strategy1.decide(state1)
            bundle2 = strategy2.decide(state2)
        except Exception as e:
            print(f"  [Frame {frame}] Strategy error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            continue

        actions1 = bundle1.to_actions()
        actions2 = bundle2.to_actions()
        _update_action_counts(player1_id, actions1)
        _update_action_counts(player2_id, actions2)

        # Process
        server.process_actions(frame, actions1, actions2)
        server._advance_buffs()
        _count_action_results(player1_id)
        _count_action_results(player2_id)

        # Progress log
        if frame % 100 == 0 or frame == 1:
            p1 = server.players[player1_id]
            p2 = server.players[player2_id]
            elapsed = time.time() - start_time
            eta = (elapsed / max(1, frame)) * (600 - frame)
            print(f"  [Frame {frame:3d}/{600}] P1@{p1.station} score={p1.total_score:3d} "
                  f"P2@{p2.station} score={p2.total_score:3d} "
                  f"phase={server.phase} [{elapsed:.1f}s elapsed, ETA {eta:.0f}s]")

    total_time = time.time() - start_time
    print(f"  [Done] {server.frame} frames in {total_time:.1f}s ({server.frame / total_time:.0f} f/s)")

    over = server.get_over_payload()
    over["_action_counts"] = action_counts
    logger1.close()
    logger2.close()
    return over


def format_player_row(pl: dict, action_counts: dict[str, int] | None = None) -> dict:
    """Extract a flat dict of key metrics from a player's over data."""
    sd = pl["scoreDetail"]
    row = {
        "playerId": pl["playerId"],
        "totalScore": pl["totalScore"],
        "delivered": pl["delivered"],
        "deliverRound": pl["deliverRound"],
        "freshness": pl["freshness"],
        "goodFruit": pl["goodFruit"],
        "badFruit": pl["badFruit"],
        "taskScore": pl["taskScore"],
        "bountyScore": pl["bountyScore"],
        "penaltyScore": pl["penaltyScore"],
        "scoreDelivery": sd["delivery"],
        "scoreGoodFruit": sd["goodFruit"],
        "scoreFreshness": sd["freshness"],
        "scoreTime": sd["time"],
        "scoreTasks": sd["tasks"],
        "scoreBounty": sd["bounty"],
        "scorePenalty": sd["penalty"],
    }
    if action_counts:
        for key in sorted(action_counts.keys()):
            row[f"act_{key}"] = action_counts[key]
    return row


def write_summary_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    """Write results to a CSV file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Summary written to {path}")


def print_summary(all_rows: list[dict]) -> None:
    """Print aggregate statistics from a list of result rows."""
    if not all_rows:
        print("  No results to summarize.")
        return

    scores = [r["totalScore"] for r in all_rows]
    delivered = [r for r in all_rows if r["delivered"]]
    deliver_rounds = [r["deliverRound"] for r in delivered if r["deliverRound"] > 0]
    freshnesses = [r["freshness"] for r in all_rows]
    good_fruits = [r["goodFruit"] for r in all_rows]
    bad_fruits = [r["badFruit"] for r in all_rows]
    task_scores = [r["taskScore"] for r in all_rows]
    bounty_scores = [r["bountyScore"] for r in all_rows]
    penalties = [r["penaltyScore"] for r in all_rows]

    scores_sorted = sorted(scores)
    n = len(scores_sorted)
    median = scores_sorted[n // 2] if n % 2 == 1 else (scores_sorted[n // 2 - 1] + scores_sorted[n // 2]) // 2

    print(f"\n{'='*60}")
    print(f"  Summary ({n} players across seeds)")
    print(f"{'-'*60}")
    print(f"  Average Score:    {sum(scores)//n:>6d}")
    print(f"  Median Score:     {median:>6d}")
    print(f"  Min Score:        {min(scores):>6d}")
    print(f"  Max Score:        {max(scores):>6d}")
    print(f"{'-'*60}")
    print(f"  Delivery Rate:    {len(delivered)/n*100:>5.1f}% ({len(delivered)}/{n})")
    if deliver_rounds:
        print(f"  Avg Deliver Rd:   {sum(deliver_rounds)//len(deliver_rounds):>6d}")
        print(f"  Earliest Deliver: {min(deliver_rounds):>6d}")
        print(f"  Latest Deliver:   {max(deliver_rounds):>6d}")
    print(f"{'-'*60}")
    print(f"  Avg Freshness:    {sum(freshnesses)/n:>6.1f}")
    print(f"  Avg Good Fruit:   {sum(good_fruits)/n:>6.1f}")
    print(f"  Avg Bad Fruit:    {sum(bad_fruits)/n:>6.1f}")
    print(f"  Avg Task Score:   {sum(task_scores)/n:>6.1f}")
    print(f"  Avg Bounty Score: {sum(bounty_scores)/n:>6.1f}")
    print(f"  Avg Penalty:      {sum(penalties)/n:>6.1f}")
    print(f"{'='*60}\n")


def parse_seed_spec(s: str) -> list[int]:
    """Parse '1-20' or '1,2,3' into a list of seeds."""
    if "-" in s and not s.startswith("--"):
        parts = s.split("-", 1)
        try:
            start, end = int(parts[0]), int(parts[1])
            return list(range(start, end + 1))
        except ValueError:
            pass
    result = []
    for part in s.split(","):
        part = part.strip()
        if part:
            result.append(int(part))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local dual-bot battle")
    parser.add_argument("--seed", type=int, default=42, help="Single random seed")
    parser.add_argument("--seeds", type=str, default=None, help="Seed range, e.g. 1-20")
    parser.add_argument("--seed-list", type=str, default=None, help="Comma-separated seeds, e.g. 1,2,3,42")
    parser.add_argument("--summary-csv", type=str, default=None, help="CSV output path")
    parser.add_argument("--log-dir", type=str, default="logs", help="Log directory")
    args = parser.parse_args()

    os.makedirs(args.log_dir, exist_ok=True)

    # Determine seed list
    if args.seed_list:
        seeds = parse_seed_spec(args.seed_list)
    elif args.seeds:
        seeds = parse_seed_spec(args.seeds)
    else:
        seeds = [args.seed]

    if not seeds:
        print("No seeds specified.")
        return 1

    all_rows: list[dict] = []
    fieldnames_seen: set[str] = set()

    for i, seed in enumerate(seeds):
        print(f"\n{'='*60}")
        print(f"Battle {i+1}/{len(seeds)}: Seed={seed}")
        print(f"{'='*60}")

        over = run_battle(seed=seed, log_dir=args.log_dir)
        od = over["msg_data"]
        action_counts = over.get("_action_counts", {})

        for pl in od["players"]:
            pid = pl["playerId"]
            counts = action_counts.get(str(pid), {})
            row = format_player_row(pl, counts)
            row["seed"] = seed
            all_rows.append(row)
            fieldnames_seen.update(row.keys())

        # Print per-battle summary with action counts
        p1, p2 = od["players"][0], od["players"][1]
        print(f"  Result: P1={p1['totalScore']} P2={p2['totalScore']} "
              f"winner={od['winnerPlayerId']} type={od['resultType']}")
        for pid_label, pid_val in [("P1", p1["playerId"]), ("P2", p2["playerId"])]:
            cnt = action_counts.get(str(pid_val), {})
            accepted = cnt.get("accepted_action", 0)
            rejected = cnt.get("rejected_action", 0)
            wait_ct = cnt.get("WAIT", 0)
            move_ct = cnt.get("MOVE", 0)
            claim_task = cnt.get("CLAIM_TASK", 0)
            claim_res = cnt.get("CLAIM_RESOURCE", 0)
            use_res = cnt.get("USE_RESOURCE", 0)
            window_ct = cnt.get("WINDOW_CARD", 0)
            abstain = cnt.get("window_card_ABSTAIN", 0)
            guard_ct = cnt.get("SET_GUARD", 0)
            break_g = cnt.get("BREAK_GUARD", 0)
            forced_p = cnt.get("FORCED_PASS", 0)
            verify_g = cnt.get("VERIFY_GATE", 0)
            deliver_ct = cnt.get("DELIVER", 0)
            empty = cnt.get("empty_action", 0)
            print(f"    {pid_label} actions: accepted={accepted} rejected={rejected} "
                  f"WAIT={wait_ct} MOVE={move_ct} empty={empty}")
            print(f"    {pid_label} details: TASK={claim_task} RES={claim_res} USE={use_res} "
                  f"WINDOW={window_ct}(ABSTAIN={abstain}) GUARD={guard_ct} BREAK={break_g} "
                  f"FORCE={forced_p} VERIFY={verify_g} DELIVER={deliver_ct}")

    # Overall summary
    print_summary(all_rows)

    # CSV output
    if args.summary_csv and all_rows:
        base_fieldnames = list(format_player_row(od["players"][0]).keys())
        action_fieldnames = sorted(name for name in fieldnames_seen if name.startswith("act_"))
        extra_fieldnames = sorted(name for name in fieldnames_seen if name not in set(base_fieldnames) | {"seed"} and not name.startswith("act_"))
        fieldnames = ["seed"] + base_fieldnames + extra_fieldnames + action_fieldnames
        write_summary_csv(args.summary_csv, all_rows, fieldnames)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
