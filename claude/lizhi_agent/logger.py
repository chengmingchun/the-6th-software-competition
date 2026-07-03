from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


class DecisionLogger:
    """Small stderr/file logger that never breaks the game loop.

    Only important decision events are printed.  Set LIZHI_DEBUG=0 to
    silence everything; LIZHI_LOG_STYLE=json for machine-friendly lines.
    """

    # Events worth showing in human-readable mode
    KEY_EVENTS = frozenset({
        "start", "strategy_start", "start_detail",
        "feedback_learn",
        "blocker_decision", "route_decision",
        "squad_eval",
        "fixed_process_eval", "fixed_process_skip",
        "stall_breaker",
        "ini_game", "strategy_variant", "opponent_pressure",
        "server_error", "message_error", "server_closed",
    })

    def __init__(self, player_id: str, log_dir: str = "logs") -> None:
        self.player_id = player_id
        self.log_dir = log_dir
        self.enabled = os.environ.get("LIZHI_DEBUG", "1") != "0"
        self.file_enabled = os.environ.get("LIZHI_FILE_LOG", "0") == "1"
        self.style = os.environ.get("LIZHI_LOG_STYLE", "pretty").lower()
        self._file = None
        self._last_round: Any = None
        self._last_stage: str | None = None
        if self.file_enabled:
            os.makedirs(log_dir, exist_ok=True)
            suffix = "jsonl" if self.style == "json" else "log"
            path = os.path.join(log_dir, f"{player_id}.{suffix}")
            self._file = open(path, "a", encoding="utf-8")

    def info(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        if fields.get("round") is not None:
            self._last_round = fields.get("round")
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "playerId": self.player_id,
            "event": event,
            **fields,
        }
        if self.style == "json":
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        else:
            # Only format key events in pretty mode
            if event not in self.KEY_EVENTS:
                return
            line = self._format_pretty(event, fields)
            if line is None:
                return
        try:
            print(line, file=sys.stderr, flush=True)
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
        except Exception:
            pass

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass

    def _format_pretty(self, event: str, fields: dict[str, Any]) -> str | None:
        round_no = fields.get("round", self._last_round)
        prefix = f"[第{round_no}帧]" if round_no is not None else "[赛前]"
        formatter = getattr(self, f"_fmt_{event}", None)
        if formatter is not None:
            try:
                return f"{prefix} {formatter(fields)}"
            except Exception:
                pass
        if event in self.KEY_EVENTS:
            text = " ".join(f"{k}={self._short(v)}" for k, v in fields.items()
                           if k != "event" and not k.startswith("_"))
            return f"{prefix} [{event}] {text}"
        return None

    def _fmt_start(self, fields: dict[str, Any]) -> str:
        return f"[开局] matchId={fields.get('matchId')}，等待第一帧调度"

    def _fmt_strategy_start(self, fields: dict[str, Any]) -> str:
        return f"[地图] 节点 {fields.get('nodes')} 路线 {fields.get('edges')} 条"

    def _fmt_start_detail(self, fields: dict[str, Any]) -> str:
        return f"[地图] 节点={fields.get('nodeCount')} 路线={fields.get('edgeCount')} 角色={fields.get('roles')}"

    def _fmt_decision(self, fields: dict[str, Any]) -> str:
        return (f"[定策] 原因={fields.get('reason')} "
                f"位置={fields.get('station')} 状态={fields.get('status')} "
                f"好果={fields.get('goodFruit')} 鲜度={fields.get('freshness')} "
                f"任务分={fields.get('taskScore')} 总分={fields.get('score')} | "
                f"动作={self._actions_text(fields.get('actions') or [])}")

    def _fmt_feedback_learn(self, fields: dict[str, Any]) -> str | None:
        learned = fields.get("learned")
        if learned == "process_required":
            return f"[回执] 服务端要求先 PROCESS：{fields.get('nodeId')}"
        if learned == "fixed_process_completed":
            return f"[回执] PROCESS 完成：{fields.get('nodeId')}"
        if learned == "fixed_process_rejected":
            return f"[回执] PROCESS 被拒：{fields.get('nodeId')} code={fields.get('code')}"
        if learned == "task_rejected":
            return f"[回执] 任务被拒：{fields.get('taskId')} code={fields.get('code')}"
        if learned == "resource_rejected":
            return f"[回执] 资源被拒：{fields.get('nodeId')} {fields.get('resourceType')}"
        return None

    def _fmt_blocker_decision(self, fields: dict[str, Any]) -> str:
        return (f"[拦路] {fields.get('target')} 有 {fields.get('blocker')}，"
                f"处理={fields.get('action')}")

    def _fmt_action_result(self, fields: dict[str, Any]) -> str:
        code = fields.get("code")
        if not code or fields.get("accepted") is not False:
            return f"[结果] {fields.get('action')} accepted"
        return f"[结果] {fields.get('action')} 被拒 code={code} node={fields.get('nodeId')}"

    def _fmt_route_decision(self, fields: dict[str, Any]) -> str:
        hop = fields.get("nextHop")
        if not hop:
            return f"[路线] {fields.get('fromNode')} => {fields.get('target')}：无路径"
        return f"[路线] {fields.get('fromNode')} -> {hop} -> {fields.get('target')}"

    def _fmt_move_decision(self, fields: dict[str, Any]) -> str:
        return f"[行军] MOVE->{fields.get('target')}"

    def _fmt_squad_eval(self, fields: dict[str, Any]) -> str | None:
        action = fields.get("action")
        if not action:
            return None
        return f"[小队] {action} -> {fields.get('target')}"

    def _fmt_fixed_process_eval(self, fields: dict[str, Any]) -> str | None:
        return f"[处理] {fields.get('station')} -> {fields.get('action')}"

    def _fmt_fixed_process_skip(self, fields: dict[str, Any]) -> str | None:
        return f"[处理] 跳过 {fields.get('station')} 原因={fields.get('reason')}"

    def _fmt_stall_breaker(self, fields: dict[str, Any]) -> str | None:
        kind = fields.get("kind")
        if kind == "station":
            return f"[破局] {fields.get('station')} 停留 {fields.get('stayFrames', '?')} 帧，{fields.get('reason')}"
        if kind == "window":
            return f"[破局] 窗口 {fields.get('objectKey')} 反复拉扯，{fields.get('reason')}"
        if kind == "object":
            return f"[破局] {fields.get('objectKey')} 冷却到第 {fields.get('cooldownUntil')} 帧"
        return None

    def _fmt_ini_game(self, fields: dict[str, Any]) -> str:
        return f"[开局] 初始好果={fields.get('goodFruit')} 鲜度={fields.get('freshness')}"

    def _fmt_strategy_variant(self, fields: dict[str, Any]) -> str:
        return f"[策略] variant={fields.get('variant')}"

    def _fmt_opponent_pressure(self, fields: dict[str, Any]) -> str:
        return (f"[对手] 对手领先，我距宫门={fields.get('myGateCost')} "
                f"对手距宫门={fields.get('opponentGateCost')}")

    def _fmt_server_error(self, fields: dict[str, Any]) -> str:
        return f"[告警] {self._short(fields.get('error'))}"

    def _fmt_message_error(self, fields: dict[str, Any]) -> str:
        return f"[告警] 消息异常：{fields.get('error')}，已兜底"

    def _fmt_server_closed(self, fields: dict[str, Any]) -> str:
        return "[收官] 服务端断连"

    def _actions_text(self, actions: Any) -> str:
        if not actions:
            return "空"
        parts = []
        for action in actions:
            if not isinstance(action, dict):
                parts.append(str(action))
                continue
            name = action.get("action")
            target = action.get("targetNodeId")
            extra = []
            for key in ("taskId", "resourceType", "contestId", "card", "rushTactic"):
                if action.get(key) is not None:
                    extra.append(f"{key}={action.get(key)}")
            suffix = f"->{target}" if target else ""
            detail = f"({', '.join(extra)})" if extra else ""
            parts.append(f"{name}{suffix}{detail}")
        return "；".join(parts)

    def _short(self, value: Any) -> str:
        text = str(value)
        return text if len(text) <= 240 else text[:237] + "..."
