#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import socket
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lizhi_server.engine import GameEngine
from lizhi_server.server import FrameCodec, MatchRunner
from tools.audit_metrics import ACTION_FIELDS, audit_frame, new_audit


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_bot(bot_dir: Path, player_id: str, port: int) -> subprocess.Popen:
    main_py = bot_dir / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"missing main.py in {bot_dir}")
    env = os.environ.copy()
    env.setdefault("LIZHI_DEBUG", "0")
    env.setdefault("LIZHI_FILE_LOG", "0")
    env.setdefault("LIZHI_RAW_LOG", "0")
    env.setdefault("LIZHI_FIXTURE_LOG", "0")
    return subprocess.Popen(
        [sys.executable, str(main_py), player_id, "127.0.0.1", str(port)],
        cwd=str(bot_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def run_match(seed: int, bot_a: Path, bot_b: Path, port: int, swap: bool = False) -> dict[str, Any]:
    red_id, blue_id = "1001", "1002"
    a_id, b_id = (blue_id, red_id) if swap else (red_id, blue_id)
    audit_a = new_audit()
    audit_b = new_audit()
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", port))
    server_sock.listen(2)
    server_sock.settimeout(30)
    proc_a = start_bot(bot_a, a_id, port)
    proc_b = start_bot(bot_b, b_id, port)
    try:
        conn_1, addr_1 = server_sock.accept()
        conn_2, addr_2 = server_sock.accept()
        codec_1 = FrameCodec(conn_1, "A?")
        codec_2 = FrameCodec(conn_2, "B?")
        reg_1 = codec_1.recv(5.0)
        reg_2 = codec_2.recv(5.0)
        registered: dict[str, tuple[Any, Any, FrameCodec]] = {}
        for conn, addr, codec, reg in ((conn_1, addr_1, codec_1, reg_1), (conn_2, addr_2, codec_2, reg_2)):
            if not reg or reg.get("msg_name") != "registration":
                raise RuntimeError(f"missing registration from {addr}: {reg!r}")
            player_id = str(reg.get("msg_data", {}).get("playerId"))
            registered[player_id] = (conn, addr, codec)
        if a_id not in registered or b_id not in registered:
            raise RuntimeError(f"registration/player mismatch: expected {a_id},{b_id}; got {sorted(registered)}")
        conn_a, addr_a, codec_a = registered[a_id]
        conn_b, addr_b, codec_b = registered[b_id]
        codec_a.name = "A"
        codec_b.name = "B"
        engine = GameEngine(match_id=f"audit_{seed}", seed=seed, player1_id=a_id, player2_id=b_id)
        runner = MatchRunner(conn_a, addr_a, conn_b, addr_b, match_id=f"audit_{seed}", seed=seed)
        runner.player1_id = a_id
        runner.player2_id = b_id
        runner.codec1 = codec_a
        runner.codec2 = codec_b
        codec_a.send(engine.get_start_payload(a_id))
        codec_b.send(engine.get_start_payload(b_id))
        codec_a.recv(5.0)
        codec_b.recv(5.0)
        for round_no in range(1, 601):
            if engine.ended:
                break
            inquire = engine.get_inquire_payload(round_no)
            codec_a.send(inquire)
            codec_b.send(inquire)
            msg_a, msg_b = runner._recv_actions_pair()
            actions_a = msg_a.get("msg_data", {}).get("actions", []) if msg_a else []
            actions_b = msg_b.get("msg_data", {}).get("actions", []) if msg_b else []
            audit_frame(audit_a, inquire, a_id, actions_a)
            audit_frame(audit_b, inquire, b_id, actions_b)
            engine.process_actions(round_no, actions_a, actions_b)
            engine._advance_buffs()
        over = engine.get_over_payload()
        codec_a.send(over)
        codec_b.send(over)
        p1 = engine.players[runner.player1_id]
        p2 = engine.players[runner.player2_id]
        audit_a["rejectedActionCount"] = int(p1.illegal_action_count)
        audit_b["rejectedActionCount"] = int(p2.illegal_action_count)
        result = {
            "seed": seed,
            "swap": swap,
            "overRound": engine.frame,
            "players": [player_result(p1, bot_a, audit_a), player_result(p2, bot_b, audit_b)],
        }
        if p1.total_score > p2.total_score:
            result["winnerBotDir"] = str(bot_a.resolve())
            result["winnerSide"] = "A"
        elif p2.total_score > p1.total_score:
            result["winnerBotDir"] = str(bot_b.resolve())
            result["winnerSide"] = "B"
        else:
            result["winnerBotDir"] = "DRAW"
            result["winnerSide"] = "DRAW"
        return result
    finally:
        for proc in (proc_a, proc_b):
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass
        try:
            server_sock.close()
        except Exception:
            pass


def player_result(player: Any, bot_dir: Path, audit: dict[str, int]) -> dict[str, Any]:
    return {
        "playerId": player.player_id,
        "teamId": player.team_id,
        "botDir": str(bot_dir.resolve()),
        "totalScore": player.total_score,
        "delivered": player.delivered,
        "deliverRound": player.deliver_round,
        "freshness": round(player.freshness, 2),
        "goodFruit": player.good_fruit,
        "badFruit": player.bad_fruit,
        "taskScore": player.task_score,
        "bountyScore": player.bounty_score,
        "penaltyScore": player.illegal_action_count,
        **audit,
    }


def parse_seeds(spec: str | None) -> list[int]:
    if not spec:
        return [42]
    spec = spec.strip()
    if "-" in spec and "," not in spec:
        start, end = spec.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(part.strip()) for part in spec.split(",") if part.strip()]


def mean(results: list[dict[str, Any]], index: int, key: str) -> float:
    values = [float(r["players"][index].get(key, 0) or 0) for r in results]
    return statistics.mean(values) if values else 0.0


def score_summary(name: str, scores: list[int]) -> str:
    return f"{name}: avg={statistics.mean(scores):.1f} median={statistics.median(scores):.1f} min={min(scores)} max={max(scores)}" if scores else f"{name}: no data"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audited head-to-head tournament runner")
    parser.add_argument("--bot-a", required=True)
    parser.add_argument("--bot-b", required=True)
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--seed-list", default=None)
    parser.add_argument("--swap-sides", action="store_true")
    parser.add_argument("--summary-csv", default=None)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    bot_a = Path(args.bot_a).resolve()
    bot_b = Path(args.bot_b).resolve()
    seeds = parse_seeds(args.seed_list or args.seeds)
    results: list[dict[str, Any]] = []
    run_no = 0
    print(f"Audited tournament: {bot_a.name} vs {bot_b.name}")
    print(f"Seeds={seeds} swap={args.swap_sides}")
    for swap in ([False, True] if args.swap_sides else [False]):
        for seed in seeds:
            run_no += 1
            port = args.port or free_port()
            print(f"[{run_no}] seed={seed} swap={swap} port={port}")
            result = run_match(seed, bot_a, bot_b, port, swap=swap)
            result["run"] = run_no
            results.append(result)
            pa, pb = result["players"]
            print(
                f"  A={pa['totalScore']} B={pb['totalScore']} "
                f"A(task={pa['taskScore']}, fresh={pa['freshness']}, idleEmpty={pa['idleEmptyCount']}, abstainHV={pa['highValueAbstainCount']}, useRes={pa['useResourceCount']}, rej={pa['rejectedActionCount']}) "
                f"B(task={pb['taskScore']}, fresh={pb['freshness']}, idleEmpty={pb['idleEmptyCount']}, abstainHV={pb['highValueAbstainCount']}, useRes={pb['useResourceCount']}, rej={pb['rejectedActionCount']})"
            )
    if not results:
        return 1
    scores_a = [r["players"][0]["totalScore"] for r in results]
    scores_b = [r["players"][1]["totalScore"] for r in results]
    wins_a = sum(1 for r in results if r.get("winnerSide") == "A")
    wins_b = sum(1 for r in results if r.get("winnerSide") == "B")
    draws = sum(1 for r in results if r.get("winnerSide") == "DRAW")
    n = len(results)
    print("\nSummary")
    print(score_summary(bot_a.name, scores_a))
    print(score_summary(bot_b.name, scores_b))
    print(f"wins: A={wins_a/n*100:.0f}% B={wins_b/n*100:.0f}% draw={draws/n*100:.0f}%")
    print("behavior averages:")
    for key in ["idleEmptyCount", "legalSystemWaitCount", "highValueAbstainCount", "abstainCount", "useResourceCount", "claimTaskCount", "claimResourceCount", "rejectedActionCount", "guardBlockedMoveResultCount", "maxGuardBlockedMoveStreak", "iceBoxUnusedLowFreshnessFrames", "horseUnusedWhileMovingFrames", "intelUnusedBeforeGateFrames"]:
        print(f"  {key:<34} A={mean(results, 0, key):6.1f} B={mean(results, 1, key):6.1f}")
    if args.summary_csv:
        base_fields = ["playerId", "teamId", "botDir", "totalScore", "delivered", "deliverRound", "freshness", "goodFruit", "badFruit", "taskScore", "bountyScore", "penaltyScore", *ACTION_FIELDS, "rejectedActionCount"]
        fieldnames = ["run", "seed", "swap", *[f"a_{field}" for field in base_fields], *[f"b_{field}" for field in base_fields], "winnerBotDir", "winnerSide"]
        rows = []
        for result in results:
            pa, pb = result["players"]
            row = {"run": result["run"], "seed": result["seed"], "swap": result["swap"], "winnerBotDir": result.get("winnerBotDir", ""), "winnerSide": result.get("winnerSide", "")}
            for field in base_fields:
                row[f"a_{field}"] = pa.get(field)
                row[f"b_{field}"] = pb.get(field)
            rows.append(row)
        os.makedirs(os.path.dirname(args.summary_csv) or ".", exist_ok=True)
        with open(args.summary_csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"csv={args.summary_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
