#!/usr/bin/env python3
"""Head-to-head tournament runner for comparing bot versions.

Launches two bots as subprocess clients connecting to a local TCP server.
Both bots are autonomous directories containing main.py + lizhi_agent/.

Usage:
    python tools/tournament_runner.py --bot-a . --bot-b claude --seeds 1-5
    python tools/tournament_runner.py --bot-a . --bot-b ../other --seed-list 42,100
    python tools/tournament_runner.py --bot-a . --bot-b claude --swap-sides --seeds 1-3
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lizhi_server.server import MatchRunner, FrameCodec, _log, FRAME_INTERVAL_MS
from lizhi_server.engine import GameEngine


def _find_free_port() -> int:
    """Ask the OS for a temporary free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_bot(bot_dir: Path, player_id: str, host: str, port: int,
               timeout: float = 30) -> subprocess.Popen:
    """Start a bot client as a subprocess."""
    main_py = bot_dir / "main.py"
    if not main_py.exists():
        # Try direct python module
        main_py = bot_dir / "run_local_battle.py"
    if not main_py.exists():
        raise FileNotFoundError(f"No main.py or run_local_battle.py in {bot_dir}")

    return subprocess.Popen(
        [sys.executable, str(main_py), player_id, host, str(port)],
        cwd=str(bot_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_single_match(seed: int, bot_a_dir: Path, bot_b_dir: Path,
                      port: int, player_a_id: str = "1001",
                      player_b_id: str = "1002",
                      swap_sides: bool = False) -> dict:
    """Run a single match between two bots via TCP subprocess.

    Returns result dict with keys: seed, players[], winnerPlayerId, etc.
    """
    id_a = player_a_id
    id_b = player_b_id

    if swap_sides:
        # Bot A plays as BLUE (id_b), Bot B plays as RED (id_a)
        bot_a_player_id, bot_b_player_id = id_b, id_a
    else:
        bot_a_player_id, bot_b_player_id = id_a, id_b

    # Create a TCP server socket
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", port))
    server_sock.listen(2)
    server_sock.settimeout(30)

    # Start bots
    proc_a = _start_bot(bot_a_dir, bot_a_player_id, "127.0.0.1", port)
    proc_b = _start_bot(bot_b_dir, bot_b_player_id, "127.0.0.1", port)

    try:
        # Accept connections
        conn_a, addr_a = server_sock.accept()
        conn_b, addr_b = server_sock.accept()

        # Create engine and runner
        engine = GameEngine(
            match_id=f"tournament_{seed}",
            seed=seed,
            player1_id=bot_a_player_id,
            player2_id=bot_b_player_id,
        )

        runner = MatchRunner(conn_a, addr_a, conn_b, addr_b,
                             match_id=f"T_{seed}", seed=seed)
        runner.player1_id = bot_a_player_id
        runner.player2_id = bot_b_player_id

        # Replace engine with our pre-created one
        runner_engine = engine

        # Run registration/start/ready manually, then game loop
        codec_a = FrameCodec(conn_a, "A")
        codec_b = FrameCodec(conn_b, "B")

        # Receive registration
        codec_a.recv(5.0)
        codec_b.recv(5.0)

        # Send start
        codec_a.send(engine.get_start_payload(bot_a_player_id))
        codec_b.send(engine.get_start_payload(bot_b_player_id))

        # Receive ready
        codec_a.recv(5.0)
        codec_b.recv(5.0)

        # Game loop
        for round_no in range(1, 601):
            if engine.ended:
                break

            inquire = engine.get_inquire_payload(round_no)
            codec_a.send(inquire)
            codec_b.send(inquire)

            # Concurrent recv
            acts_a, acts_b = runner._recv_actions_pair()

            a1 = acts_a.get("msg_data", {}).get("actions", []) if acts_a else []
            a2 = acts_b.get("msg_data", {}).get("actions", []) if acts_b else []

            engine.process_actions(round_no, a1, a2)
            engine._advance_buffs()

        # Send over
        over = engine.get_over_payload()
        codec_a.send(over)
        codec_b.send(over)

        # Collect result
        p1 = engine.players[runner.player1_id]
        p2 = engine.players[runner.player2_id]

        result = {
            "seed": seed,
            "overRound": engine.frame,
            "players": [
                {
                    "playerId": p1.player_id,
                    "teamId": p1.team_id,
                    "botDir": str(bot_a_dir.resolve()),
                    "totalScore": p1.total_score,
                    "delivered": p1.delivered,
                    "deliverRound": p1.deliver_round,
                    "freshness": round(p1.freshness, 2),
                    "goodFruit": p1.good_fruit,
                    "badFruit": p1.bad_fruit,
                    "taskScore": p1.task_score,
                    "bountyScore": p1.bounty_score,
                    "penaltyScore": p1.illegal_action_count,
                },
                {
                    "playerId": p2.player_id,
                    "teamId": p2.team_id,
                    "botDir": str(bot_b_dir.resolve()),
                    "totalScore": p2.total_score,
                    "delivered": p2.delivered,
                    "deliverRound": p2.deliver_round,
                    "freshness": round(p2.freshness, 2),
                    "goodFruit": p2.good_fruit,
                    "badFruit": p2.bad_fruit,
                    "taskScore": p2.task_score,
                    "bountyScore": p2.bounty_score,
                    "penaltyScore": p2.illegal_action_count,
                },
            ],
        }

        # Determine winner
        if p1.total_score > p2.total_score:
            result["winnerPlayerId"] = p1.player_id
            result["winnerBotDir"] = str(bot_a_dir.resolve())
        elif p2.total_score > p1.total_score:
            result["winnerPlayerId"] = p2.player_id
            result["winnerBotDir"] = str(bot_b_dir.resolve())
        else:
            result["winnerPlayerId"] = None
            result["winnerBotDir"] = "DRAW"

        return result

    finally:
        # Cleanup
        for p in [proc_a, proc_b]:
            try:
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass
        try:
            server_sock.close()
        except Exception:
            pass


def parse_seeds(spec: str | None) -> list[int]:
    """Parse '1-20' or '1,42,100' into seed list."""
    if not spec:
        return [42]
    spec = spec.strip()
    if "-" in spec and "," not in spec:
        parts = spec.split("-", 1)
        return list(range(int(parts[0]), int(parts[1]) + 1))
    return [int(p.strip()) for p in spec.split(",") if p.strip()]


def summarize(label: str, scores: list[int]) -> str:
    if not scores:
        return f"{label}: no data"
    return (f"{label}: n={len(scores)} avg={statistics.mean(scores):.1f} "
            f"median={statistics.median(scores):.1f} "
            f"range=[{min(scores)},{max(scores)}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Head-to-head tournament runner")
    parser.add_argument("--bot-a", required=True, type=str, help="Path to bot A directory")
    parser.add_argument("--bot-b", required=True, type=str, help="Path to bot B directory")
    parser.add_argument("--seeds", type=str, default=None, help="Seed range (1-20)")
    parser.add_argument("--seed-list", type=str, default=None, help="Seed list (1,42,100)")
    parser.add_argument("--swap-sides", action="store_true", help="Also run with swapped sides")
    parser.add_argument("--summary-csv", type=str, default=None, help="CSV output path")
    parser.add_argument("--port", type=int, default=0, help="TCP port (0=auto)")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat each seed N times")
    args = parser.parse_args(argv)

    seeds = parse_seeds(args.seed_list or args.seeds)
    if not seeds:
        print("No seeds specified.")
        return 1

    bot_a_dir = Path(args.bot_a).resolve()
    bot_b_dir = Path(args.bot_b).resolve()

    print(f"\n{'='*60}")
    print(f"Tournament: {bot_a_dir.name} vs {bot_b_dir.name}")
    print(f"  Bot A: {bot_a_dir}")
    print(f"  Bot B: {bot_b_dir}")
    print(f"  Seeds: {seeds}")
    print(f"  Swap sides: {args.swap_sides}")
    print(f"{'='*60}\n")

    all_results: list[dict] = []

    run_index = 0
    for repeat in range(args.repeat):
        for swap in [False, True] if args.swap_sides else [False]:
            for seed in seeds:
                run_index += 1
                port = args.port if args.port else _find_free_port()
                side_label = "swapped" if swap else "normal"

                print(f"  [{run_index}] seed={seed} port={port} sides={side_label}")
                sys.stdout.flush()

                try:
                    result = _run_single_match(
                        seed, bot_a_dir, bot_b_dir, port,
                        player_a_id="1001", player_b_id="1002",
                        swap_sides=swap,
                    )
                    result["run"] = run_index
                    result["swap"] = swap
                    result["repeat"] = repeat
                    all_results.append(result)

                    p1 = result["players"][0]
                    p2 = result["players"][1]
                    print(f"    A={p1['totalScore']} B={p2['totalScore']} "
                          f"winner={result.get('winnerBotDir','?')} "
                          f"frames={result['overRound']}")
                except Exception as e:
                    print(f"    FAILED: {e}")
                    import traceback
                    traceback.print_exc()

                sys.stdout.flush()

    # Summary
    if all_results:
        scores_a = [r["players"][0]["totalScore"] for r in all_results]
        scores_b = [r["players"][1]["totalScore"] for r in all_results]
        wins_a = sum(1 for r in all_results
                     if r.get("winnerBotDir") == str(bot_a_dir.resolve()))
        wins_b = sum(1 for r in all_results
                     if r.get("winnerBotDir") == str(bot_b_dir.resolve()))
        draws = sum(1 for r in all_results
                    if r.get("winnerBotDir") == "DRAW")
        delivered_a = sum(1 for r in all_results if r["players"][0]["delivered"])
        delivered_b = sum(1 for r in all_results if r["players"][1]["delivered"])

        n = len(all_results)
        print(f"\n{'='*60}")
        print(f"  Tournament Summary ({n} matches)")
        print(f"{'-'*60}")
        print(f"  {summarize(bot_a_dir.name, scores_a)}")
        print(f"  {summarize(bot_b_dir.name, scores_b)}")
        print(f"{'-'*60}")
        print(f"  Win rate:    A={wins_a/n*100:.0f}%  B={wins_b/n*100:.0f}%  draw={draws/n*100:.0f}%")
        print(f"  Deliver:     A={delivered_a/n*100:.0f}%  B={delivered_b/n*100:.0f}%")
        print(f"{'='*60}\n")

        # CSV output
        if args.summary_csv:
            fieldnames = ["run", "seed", "swap", "repeat",
                          "a_playerId", "a_teamId", "a_botDir", "a_totalScore",
                          "a_delivered", "a_deliverRound", "a_freshness",
                          "a_goodFruit", "a_taskScore", "a_bountyScore",
                          "b_playerId", "b_teamId", "b_botDir", "b_totalScore",
                          "b_delivered", "b_deliverRound", "b_freshness",
                          "b_goodFruit", "b_taskScore", "b_bountyScore",
                          "winnerBotDir"]
            rows = []
            for r in all_results:
                pa, pb = r["players"][0], r["players"][1]
                rows.append({
                    "run": r["run"], "seed": r["seed"], "swap": r["swap"],
                    "repeat": r.get("repeat", 0),
                    "a_playerId": pa["playerId"], "a_teamId": pa["teamId"],
                    "a_botDir": pa["botDir"], "a_totalScore": pa["totalScore"],
                    "a_delivered": pa["delivered"],
                    "a_deliverRound": pa["deliverRound"],
                    "a_freshness": pa["freshness"],
                    "a_goodFruit": pa["goodFruit"],
                    "a_taskScore": pa["taskScore"],
                    "a_bountyScore": pa["bountyScore"],
                    "b_playerId": pb["playerId"], "b_teamId": pb["teamId"],
                    "b_botDir": pb["botDir"], "b_totalScore": pb["totalScore"],
                    "b_delivered": pb["delivered"],
                    "b_deliverRound": pb["deliverRound"],
                    "b_freshness": pb["freshness"],
                    "b_goodFruit": pb["goodFruit"],
                    "b_taskScore": pb["taskScore"],
                    "b_bountyScore": pb["bountyScore"],
                    "winnerBotDir": r.get("winnerBotDir", ""),
                })
            os.makedirs(os.path.dirname(args.summary_csv) or ".", exist_ok=True)
            with open(args.summary_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print(f"  CSV written to {args.summary_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
