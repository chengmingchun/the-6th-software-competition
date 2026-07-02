from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


class DecisionLogger:
    """Small stderr/file logger that never breaks the game loop.

    Default output is a Chinese, human-readable match diary.  Set
    LIZHI_LOG_STYLE=json when a script needs the old machine-friendly JSON
    lines.
    """

    def __init__(self, player_id: str, log_dir: str = "logs") -> None:
        self.player_id = player_id
        self.log_dir = log_dir
        self.enabled = os.environ.get("LIZHI_DEBUG", "1") != "0"
        self.file_enabled = os.environ.get("LIZHI_FILE_LOG", "0") == "1"
        self.style = os.environ.get("LIZHI_LOG_STYLE", "pretty").lower()
        self._file = None
        self._last_round: Any = None
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
            line = self._format_pretty(event, fields)
        try:
            print(line, file=sys.stderr, flush=True)
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
        except Exception:
            # Debug output must never crash the client.
            pass

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass

    def _format_pretty(self, event: str, fields: dict[str, Any]) -> str:
        round_no = fields.get("round", self._last_round)
        prefix = f"[第{round_no}帧]" if round_no is not None else "[赛前]"
        formatter = getattr(self, f"_fmt_{event}", None)
        if formatter is not None:
            try:
                return f"{prefix} {formatter(fields)}"
            except Exception:
                pass
        summary = " ".join(f"{key}={self._short(value)}" for key, value in fields.items())
        return f"{prefix} [记录] {event} | {summary}"

    def _fmt_connect(self, fields: dict[str, Any]) -> str:
        return f"[连线] 准备连接比赛服 {fields.get('host')}:{fields.get('port')}"

    def _fmt_send_registration(self, fields: dict[str, Any]) -> str:
        return f"[报名] 提交队伍身份 playerId={fields.get('playerId')}"

    def _fmt_recv_message(self, fields: dict[str, Any]) -> str:
        return (
            f"[收信] 收到 {fields.get('msgName')} | 阶段={fields.get('phase')} "
            f"任务={fields.get('tasks')} 窗口={fields.get('contests')} 事件={fields.get('events')}"
        )

    def _fmt_send_message(self, fields: dict[str, Any]) -> str:
        actions = fields.get("actions") or []
        return f"[发令] 发送 {fields.get('msgName')} | 动作={self._actions_text(actions)}"

    def _fmt_strategy_start(self, fields: dict[str, Any]) -> str:
        return f"[开局] 地图装入完成：节点 {fields.get('nodes')} 个，路线 {fields.get('edges')} 条"

    def _fmt_start(self, fields: dict[str, Any]) -> str:
        return f"[开局] matchId={fields.get('matchId')}，等待第一帧调度"

    def _fmt_start_detail(self, fields: dict[str, Any]) -> str:
        return (
            f"[地图] 开局载入：节点={fields.get('nodeCount')} 路线={fields.get('edgeCount')} "
            f"角色点={fields.get('roles')} gameplay={fields.get('gameplayKeys')}"
        )

    def _fmt_inquire_detail(self, fields: dict[str, Any]) -> str:
        player = fields.get("myPlayer")
        node = player.get("currentNodeId") if isinstance(player, dict) else None
        state = player.get("state") if isinstance(player, dict) else None
        return (
            f"[局势] 我方状态={state} 位置={node} "
            f"任务={len(fields.get('tasks') or [])} 窗口={len(fields.get('contests') or [])} "
            f"事件={len(fields.get('events') or [])} 结果={len(fields.get('actionResults') or [])}"
        )

    def _fmt_handle_message(self, fields: dict[str, Any]) -> str:
        return f"[分拣] 处理 {fields.get('msgName')}，字段={fields.get('msgDataKeys')}"

    def _fmt_stdio_mode(self, fields: dict[str, Any]) -> str:
        return "[本地] JSON Lines 调试模式启动，等待输入 fixture"

    def _fmt_state_snapshot(self, fields: dict[str, Any]) -> str:
        resources = fields.get("resources") or {}
        return (
            f"[车队] 状态={fields.get('status')}({fields.get('stateClass')}) "
            f"位置={fields.get('station')} 目标={fields.get('target')} "
            f"验核={self._yesno(fields.get('verified'))} 交付={self._yesno(fields.get('delivered'))} | "
            f"好果={fields.get('goodFruit')} 坏果={fields.get('badFruit')} 鲜度={fields.get('freshness')} "
            f"任务分={fields.get('taskScore')} 总分={fields.get('totalScore')} | "
            f"库存={self._stock_text(resources)} | "
            f"任务={fields.get('tasks')} 资源点={fields.get('resourcesOnMap')} 窗口={fields.get('windows')} | "
            f"到宫门≈{self._cost(fields.get('gateCost'))}帧 剩余={fields.get('turnsLeft')}帧"
        )

    def _fmt_task_eval_station(self, fields: dict[str, Any]) -> str:
        candidates = fields.get("candidates") or []
        if not candidates:
            return f"[算盘] 当前站 {fields.get('station')} 没有可接皇榜任务"
        return f"[算盘] 当前站任务候选：{self._candidate_text(candidates, 'taskId')} | 选 {fields.get('chosen')}"

    def _fmt_resource_eval_station(self, fields: dict[str, Any]) -> str:
        candidates = fields.get("candidates") or []
        if not candidates:
            return f"[货仓] 当前站 {fields.get('station')} 没有可拿资源"
        return f"[货仓] 当前站资源候选：{self._candidate_text(candidates, 'resourceType')} | 选 {fields.get('chosen')}"

    def _fmt_task_eval_reachable(self, fields: dict[str, Any]) -> str:
        candidates = fields.get("candidates") or []
        if not candidates:
            return "[算盘] 路上没有值得绕行的皇榜任务"
        return f"[算盘] 绕路任务前五：{self._candidate_text(candidates, 'taskId')} | 选 {fields.get('chosen')} 值={fields.get('chosenValue')}"

    def _fmt_resource_eval_reachable(self, fields: dict[str, Any]) -> str:
        candidates = fields.get("candidates") or []
        if not candidates:
            return "[货仓] 路上没有值得绕行的资源点"
        return f"[货仓] 绕路资源前五：{self._candidate_text(candidates, 'resourceType')} | 选 {fields.get('chosen')}@{fields.get('chosenStation')}"

    def _fmt_strategy_step(self, fields: dict[str, Any]) -> str:
        step = fields.get("step")
        if step == "window_card":
            style = fields.get("windowStyle")
            reason = fields.get("choiceReason")
            tail = f" | {style}：{reason}" if style or reason else ""
            roll = f" roll={fields.get('roll')}" if fields.get("roll") is not None else ""
            return (
                f"[出牌] 窗口={fields.get('contestType')} 目标={fields.get('target')} "
                f"第{fields.get('roundIndex')}拍，出 {fields.get('chosenCard')}{roll}{tail}"
            )
        if step in {"endgame_guard", "delivery_guard"}:
            return "[急行] 已触发终局保护，暂停贪心，优先奔向宫门/终点"
        return f"[策略] {step} | {self._short(fields)}"

    def _fmt_squad_eval(self, fields: dict[str, Any]) -> str:
        if fields.get("action"):
            return f"[小队] 派出 {fields.get('action')} -> {fields.get('target')}，提前探路压缩读条"
        return "[小队] 暂不派出，小队留作后手"

    def _fmt_route_decision(self, fields: dict[str, Any]) -> str:
        if fields.get("nextHop") is None:
            return f"[岔路] 从 {fields.get('fromNode')} 找不到去 {fields.get('target')} 的路，原地保守"
        return f"[岔路] 目标={fields.get('target')}，下一跳={fields.get('nextHop')}，车队拔营"

    def _fmt_blocker_decision(self, fields: dict[str, Any]) -> str:
        return (
            f"[拦路] {fields.get('target')} 有 {fields.get('blocker')}，"
            f"处理={fields.get('action')} {('任务=' + str(fields.get('taskId'))) if fields.get('taskId') else ''}"
        )

    def _fmt_move_decision(self, fields: dict[str, Any]) -> str:
        return f"[行军] 向 {fields.get('target')} 前进"

    def _fmt_stall_breaker(self, fields: dict[str, Any]) -> str:
        kind = fields.get("kind")
        if kind == "station":
            return (
                f"[破局] {fields.get('station')} 停留 {fields.get('stayFrames', '?')} 帧，"
                f"{fields.get('reason')}；动作={fields.get('action')}，冷却到第 {fields.get('escapeUntil')} 帧"
            )
        if kind == "window":
            return (
                f"[破局] 争抢窗口 {fields.get('objectKey')} 反复拉扯，"
                f"{fields.get('reason')}；本拍出 {fields.get('action')}"
            )
        if kind == "object":
            return (
                f"[破局] 对象 {fields.get('objectKey')} 暂时放弃，"
                f"原因={fields.get('reason')}，冷却到第 {fields.get('cooldownUntil')} 帧"
            )
        return f"[破局] {self._short(fields)}"

    def _fmt_decision(self, fields: dict[str, Any]) -> str:
        return f"[定策] 原因={fields.get('reason')} | 最终动作={self._actions_text(fields.get('actions') or [])}"

    def _fmt_server_error(self, fields: dict[str, Any]) -> str:
        return f"[告警] 服务端返回错误：{self._short(fields.get('error'))}"

    def _fmt_message_error(self, fields: dict[str, Any]) -> str:
        return f"[告警] 处理消息异常：{fields.get('error')}，已准备兜底动作"

    def _fmt_server_closed(self, fields: dict[str, Any]) -> str:
        return "[收官] 服务端断开连接，本局客户端退出"

    def _actions_text(self, actions: Any) -> str:
        if not actions:
            return "空动作(让系统继续推进)"
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
            detail = f" ({', '.join(extra)})" if extra else ""
            parts.append(f"{name}{suffix}{detail}")
        return "；".join(parts)

    def _candidate_text(self, candidates: Any, name_key: str) -> str:
        if not isinstance(candidates, list):
            return self._short(candidates)
        parts = []
        for item in candidates[:5]:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            name = item.get(name_key) or item.get("template") or item.get("station")
            value = item.get("value", item.get("rank", item.get("priority", item.get("score"))))
            target = item.get("target") or item.get("station")
            where = f"@{target}" if target else ""
            parts.append(f"{name}{where}:{value}")
        return "，".join(parts)

    def _stock_text(self, stock: Any) -> str:
        if not isinstance(stock, dict) or not stock:
            return "无"
        return ",".join(f"{key}x{value}" for key, value in stock.items() if value)

    def _yesno(self, value: Any) -> str:
        return "是" if value else "否"

    def _cost(self, value: Any) -> str:
        if value is None:
            return "?"
        if isinstance(value, int) and value >= 10**8:
            return "不可达"
        return str(value)

    def _short(self, value: Any) -> str:
        text = str(value)
        return text if len(text) <= 240 else text[:237] + "..."
