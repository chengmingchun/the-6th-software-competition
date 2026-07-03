from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


class DecisionLogger:
    """Compact key-event logger for Claude bot.

    It shows decisions, window cards, resources, squad actions, fixed process,
    rejected results, and start/end. Protocol noise stays hidden unless
    LIZHI_LOG_MODE=verbose or LIZHI_LOG_STYLE=json is set.
    """

    KEY_EVENTS = frozenset({
        "connect", "registration_sent", "start", "strategy_start", "strategy_variant",
        "decision", "action_result", "feedback_learn",
        "strategy_step", "resource_use", "resource_use_skip",
        "rush_tactic", "rush_tactic_skip", "blocker_decision",
        "fixed_process_eval", "squad_eval", "stall_breaker",
        "server_error", "message_error", "server_closed", "over",
        "stdio_mode", "stdio_error", "fixture_log_error", "opponent_pressure",
    })

    NOISY_EVENTS = frozenset({
        "recv_message", "send_message", "frame_sent", "handle_message",
        "start_detail", "inquire_detail", "state_snapshot",
        "task_eval_station", "task_eval_reachable",
        "resource_eval_station", "resource_eval_reachable",
        "route_decision", "move_decision", "fixed_process_skip",
        "feedback_ignore", "squad_eval_skip",
    })

    def __init__(self, player_id: str, log_dir: str = "logs") -> None:
        self.player_id = player_id
        self.log_dir = os.environ.get("LIZHI_LOG_DIR", log_dir)
        self.enabled = os.environ.get("LIZHI_DEBUG", "1") != "0"
        self.file_enabled = os.environ.get("LIZHI_FILE_LOG", "0") == "1"
        self.style = os.environ.get("LIZHI_LOG_STYLE", "pretty").lower()
        self.mode = os.environ.get("LIZHI_LOG_MODE", "brief").lower()
        self._file = None
        self._last_round: Any = None
        self._last_decision_signature: tuple[Any, ...] | None = None
        if self.file_enabled:
            os.makedirs(self.log_dir, exist_ok=True)
            suffix = "jsonl" if self.style == "json" else "log"
            self._file = open(os.path.join(self.log_dir, f"{player_id}.{suffix}"), "a", encoding="utf-8")

    def info(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        if fields.get("round") is not None:
            self._last_round = fields.get("round")
        if not self._should_emit(event, fields):
            return
        record = {"ts": datetime.now(timezone.utc).isoformat(), "playerId": self.player_id, "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) if self.style == "json" else self._format_pretty(event, fields)
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

    def _should_emit(self, event: str, fields: dict[str, Any]) -> bool:
        if self.mode in {"verbose", "debug", "full"}:
            return True
        if event == "decision":
            return self._should_emit_decision(fields)
        if event == "action_result":
            code = str(fields.get("code") or "").upper()
            return fields.get("accepted") is False or code not in {"", "NONE", "ACCEPTED", "OK", "SUCCESS"}
        if event == "strategy_step":
            return fields.get("step") == "window_card"
        if event == "squad_eval":
            return bool(fields.get("action"))
        if event in self.NOISY_EVENTS:
            return False
        return event in self.KEY_EVENTS

    def _should_emit_decision(self, fields: dict[str, Any]) -> bool:
        actions = fields.get("actions") or []
        round_no = fields.get("round")
        phase = fields.get("phase")
        station = fields.get("station")
        target = fields.get("target")
        status = fields.get("status")
        reason = fields.get("reason")
        task_score = fields.get("taskScore")
        freshness_bucket = self._freshness_bucket(fields.get("freshness"))
        action_text = self._actions_text(actions)
        if actions:
            self._last_decision_signature = (phase, station, target, status, action_text, reason, task_score, freshness_bucket)
            return True
        signature = (phase, station, target, status, reason, task_score, freshness_bucket)
        heartbeat = isinstance(round_no, int) and round_no % 25 == 0
        if heartbeat or signature != self._last_decision_signature:
            self._last_decision_signature = signature
            return True
        return False

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
            text = " ".join(f"{key}={self._short(value)}" for key, value in fields.items() if not key.startswith("_"))
            return f"{prefix} [{event}] {text}"
        return None

    def _fmt_connect(self, fields: dict[str, Any]) -> str:
        return f"[连线] {fields.get('host')}:{fields.get('port')}"

    def _fmt_registration_sent(self, fields: dict[str, Any]) -> str:
        return f"[报名] playerId={fields.get('playerId')}"

    def _fmt_start(self, fields: dict[str, Any]) -> str:
        return f"[开局] matchId={fields.get('matchId')}"

    def _fmt_strategy_start(self, fields: dict[str, Any]) -> str:
        return f"[地图] 节点={fields.get('nodes')} 路线={fields.get('edges')}"

    def _fmt_strategy_variant(self, fields: dict[str, Any]) -> str:
        return f"[策略] variant={fields.get('variant')} base={fields.get('base')} target={fields.get('targetTaskScore')}/{fields.get('competitiveTaskScore')}/{fields.get('greedTaskScore')}"

    def _fmt_decision(self, fields: dict[str, Any]) -> str:
        parts = [
            f"[行动] phase={fields.get('phase')}",
            f"位置={fields.get('station')}->{fields.get('target')}",
            f"状态={fields.get('status')}",
            f"分={fields.get('score')}",
            f"任务={fields.get('taskScore')}",
            f"鲜={self._num(fields.get('freshness'))}",
        ]
        if fields.get("goodFruit") is not None:
            parts.append(f"好果={fields.get('goodFruit')}")
        if fields.get("resources") is not None:
            parts.append(f"库存={self._stock_text(fields.get('resources'))}")
        return " ".join(parts) + f" | {self._actions_text(fields.get('actions') or [])} | reason={fields.get('reason')}"

    def _fmt_strategy_step(self, fields: dict[str, Any]) -> str:
        return f"[出牌] 窗口={fields.get('contestType')} 目标={fields.get('target')} 第{fields.get('roundIndex')}拍 -> {fields.get('chosenCard')} ({fields.get('windowStyle')}:{fields.get('choiceReason')})"

    def _fmt_resource_use(self, fields: dict[str, Any]) -> str:
        return f"[道具] 使用 {fields.get('resourceType')} reason={fields.get('reason')} 鲜={self._num(fields.get('freshness'))} 任务={fields.get('taskScore')} target={fields.get('target')}"

    def _fmt_resource_use_skip(self, fields: dict[str, Any]) -> str:
        return f"[道具] 跳过 {fields.get('resourceType')} reason={fields.get('reason')}"

    def _fmt_action_result(self, fields: dict[str, Any]) -> str:
        return f"[回执] {fields.get('action')} accepted={fields.get('accepted')} code={fields.get('code')} node={fields.get('nodeId')} task={fields.get('taskId')} resource={fields.get('resourceType')}"

    def _fmt_feedback_learn(self, fields: dict[str, Any]) -> str | None:
        learned = fields.get("learned")
        if learned == "fixed_process_completed":
            return f"[学习] 固定处理完成：{fields.get('nodeId')}"
        if learned == "task_rejected":
            return f"[学习] 任务被拒：{fields.get('taskId')} code={fields.get('code')}"
        if learned == "resource_rejected":
            return f"[学习] 资源被拒：{fields.get('nodeId')} {fields.get('resourceType')} code={fields.get('code')}"
        return f"[学习] {learned}"

    def _fmt_fixed_process_eval(self, fields: dict[str, Any]) -> str:
        return f"[固定处理] {fields.get('station')} -> {fields.get('action')} reason={fields.get('reason')}"

    def _fmt_squad_eval(self, fields: dict[str, Any]) -> str | None:
        if not fields.get("action"):
            return None
        return f"[小队] {fields.get('action')} -> {fields.get('target')} reason={fields.get('reason')}"

    def _fmt_stall_breaker(self, fields: dict[str, Any]) -> str | None:
        return f"[破局] kind={fields.get('kind')} target={fields.get('objectKey') or fields.get('station')} reason={fields.get('reason')}"

    def _fmt_server_error(self, fields: dict[str, Any]) -> str:
        return f"[告警] {self._short(fields.get('error'))}"

    def _fmt_message_error(self, fields: dict[str, Any]) -> str:
        return f"[告警] 消息异常：{fields.get('error')}"

    def _fmt_server_closed(self, fields: dict[str, Any]) -> str:
        return f"[收官] 服务端断开 lastRound={fields.get('lastRound')} sentActions={fields.get('sentActions')}"

    def _fmt_over(self, fields: dict[str, Any]) -> str:
        return "[收官] 比赛结束 " + self._json_short(fields.get("result"), 1200)

    def _actions_text(self, actions: Any) -> str:
        if not actions:
            return "空动作/系统推进"
        parts = []
        for action in actions:
            if not isinstance(action, dict):
                parts.append(str(action))
                continue
            name = action.get("action")
            target = action.get("targetNodeId")
            extra = []
            for key in ("taskId", "resourceType", "contestId", "card", "rushTactic", "extraGoodFruit"):
                if action.get(key) is not None:
                    extra.append(f"{key}={action.get(key)}")
            suffix = f"->{target}" if target else ""
            detail = f"({', '.join(extra)})" if extra else ""
            parts.append(f"{name}{suffix}{detail}")
        return "；".join(parts)

    def _stock_text(self, stock: Any) -> str:
        if not isinstance(stock, dict) or not stock:
            return "无"
        parts = [f"{key}x{value}" for key, value in stock.items() if value]
        return ",".join(parts) if parts else "无"

    def _num(self, value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.1f}"
        return str(value)

    def _freshness_bucket(self, value: Any) -> Any:
        try:
            return int(float(value) // 5)
        except Exception:
            return value

    def _short(self, value: Any) -> str:
        text = str(value)
        return text if len(text) <= 240 else text[:237] + "..."

    def _json_short(self, value: Any, limit: int = 3000) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
            except Exception:
                text = str(value)
        return text if len(text) <= limit else text[:limit] + f"...<truncated {len(text) - limit} chars>"
