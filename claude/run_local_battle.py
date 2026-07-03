#!/usr/bin/env python3
"""Local dual-bot battle: run two baseline strategies against each other.

Usage:
    python run_local_battle.py [--seed SEED] [--fast] [--log-dir DIR]

With --fast, runs at maximum speed with no frame-by-frame delay.
Logs are written to logs/ for later analysis.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Path setup: claude dir first (for lizhi_agent), parent for lizhi_server
CLAUDE_DIR = Path(__file__).resolve().parent
ROOT_DIR = CLAUDE_DIR.parent
if str(CLAUDE_DIR) not in sys.path:
    sys.path.insert(0, str(CLAUDE_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from lizhi_server.engine import GameEngine as ServerEngine

# Import the baseline strategy
from lizhi_agent.actions import MainActionType, SquadActionType, WindowCard, wait
from lizhi_agent.config import StrategyConfig
from lizhi_agent.logger import DecisionLogger
from lizhi_agent.models import parse_game_state, GameState
from lizhi_agent.strategy import RoadMasterStrategy


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
        return data

    # Build nodes
    nodes = []
    for nid, sinfo in server.stations.items():
        node = {
            "nodeId": nid,
            "name": sinfo.get("name", ""),
            "nodeType": sinfo.get("nodeType", ""),
            "type": sinfo.get("type", ""),
            "hasObstacle": sinfo.get("hasObstacle", False),
            "canWindow": True,
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
        # Determine if this player participates
        for pid, p in [(server.player1_id, p1), (server.player2_id, p2)]:
            if p.team_id == "RED":
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
    action_results = [r for r in server.action_results]

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
               log_dir: str = "logs", fast: bool = True) -> dict:
    """Run a full match between two baseline strategies.

    Returns the over payload dict with results.
    """
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
    strategy1 = RoadMasterStrategy(player_id=player1_id, config=config, logger=logger1)
    strategy2 = RoadMasterStrategy(player_id=player2_id, config=config, logger=logger2)

    strategy1.on_start(start_data)
    strategy2.on_start(start_data)

    start_time = time.time()

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

        # Process
        server.process_actions(frame, actions1, actions2)
        server._advance_buffs()

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
    logger1.close()
    logger2.close()
    return over


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local dual-bot battle")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--log-dir", type=str, default="logs", help="Log directory")
    args = parser.parse_args()

    os.makedirs(args.log_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Local Battle: Baseline vs Baseline")
    print(f"   Seed: {args.seed}")
    print(f"{'='*60}\n")

    over = run_battle(seed=args.seed, log_dir=args.log_dir)

    od = over["msg_data"]
    print(f"\n{'='*60}")
    print(f"[RESULT] Match Result")
    print(f"   Type: {od['resultType']} | Reason: {od['overReason']}")
    print(f"   Winner: {od['winnerPlayerId']}")
    print(f"   Frames: {od['overRound']}")
    print(f"{'-'*60}")
    for pl in od["players"]:
        sd = pl["scoreDetail"]
        print(f"   Player {pl['playerId']}:")
        print(f"     Total Score: {pl['totalScore']}")
        print(f"     Delivered: {pl['delivered']} (round {pl['deliverRound']})")
        print(f"     Freshness: {pl['freshness']:.1f}  |  Good Fruit: {pl['goodFruit']}  |  Bad Fruit: {pl['badFruit']}")
        print(f"     Delivery: {sd['delivery']}  |  Fruit: {sd['goodFruit']}  |  Fresh: {sd['freshness']}  |  Time: {sd['time']}")
        print(f"     Tasks: {sd['tasks']}  |  Bounty: {sd['bounty']}  |  Penalty: {sd['penalty']}")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
