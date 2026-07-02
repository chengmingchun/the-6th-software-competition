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
        self.stage_banner_enabled = os.environ.get("LIZHI_STAGE_BANNER", "1") != "0"
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
        banner = None
        if self.style == "json":
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        else:
            banner = self._stage_banner(event, fields)
            line = self._format_pretty(event, fields)
        try:
            if banner is not None:
                print(banner, file=sys.stderr, flush=True)
                if self._file is not None:
                    self._file.write(banner + "\n")
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

    def _stage_banner(self, event: str, fields: dict[str, Any]) -> str | None:
        if not self.stage_banner_enabled or event != "state_snapshot":
            return None
        stage = self._stage_name(fields)
        if stage == self._last_stage:
            return None
        self._last_stage = stage
        round_no = fields.get("round", self._last_round)
        return f"\n==================== ==阶段：{stage}｜第{round_no}帧== ===================="

    def _stage_name(self, fields: dict[str, Any]) -> str:
        phase = str(fields.get("phase") or "UNKNOWN").upper()
        status = str(fields.get("status") or "UNKNOWN").upper()
        station = fields.get("station")
        verified = bool(fields.get("verified"))
        delivered = bool(fields.get("delivered"))
        task_score = fields.get("taskScore")
        escape_until = fields.get("stationEscapeUntil")

        if delivered or status == "DELIVERED":
            return "已交付收尾"
        if phase in {"RUSH", "BANQUET", "ENDGAME", "FINAL", "宫宴冲刺"}:
            if verified:
                return "冲刺期：已验核奔终点"
            return "冲刺期：赶赴宫门验核"
        if status in {"MOVING", "WAITING"}:
            return "行军中：等待系统推进"
        if status in {"PROCESSING", "VERIFYING", "RESTING", "FORCED_PASSING", "CONTESTING"}:
            return "忙碌读条：处理/验核/休整"
        if escape_until not in (None, "", 0):
            return "破局逃逸：放弃支线回主线"
        if verified:
            return "已验核：冲向终点"
        if station == "S14":
            return "宫门前：等待/执行验核"
        if isinstance(task_score, int) and task_score < 90:
            return "前期攒分：任务资源取舍"
        return "主线推进：赶赴宫门"

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

    def _fmt_frame_sent(self, fields: dict[str, Any]) -> str:
        return (
            f"[出包] {fields.get('msgName')} 已写入 socket，"
            f"prefix={fields.get('prefix')} body={fields.get('bodyBytes')}B frame={fields.get('frameBytes')}B"
        )

    def _fmt_registration_sent(self, fields: dict[str, Any]) -> str:
        return f"[报名] registration 已发送，playerId={fields.get('playerId')}"

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

    def _fmt_feedback_learn(self, fields: dict[str, Any]) -> str:
        learned = fields.get("learned")
        if learned == "fixed_process_completed":
            return f"[回执] 固定处理完成：{fields.get('nodeId')}，本次到站不再重复 PROCESS"
        if learned == "fixed_process_rejected":
            return f"[回执] 固定处理被拒：{fields.get('nodeId')} code={fields.get('code')}，加入跳过列表"
        if learned == "task_rejected":
            return f"[回执] 任务被拒：{fields.get('taskId')} code={fields.get('code')}，加入冷却/黑名单"
        if learned == "resource_rejected":
            return f"[回执] 资源领取被拒：{fields.get('nodeId')} {fields.get('resourceType')} code={fields.get('code')}，加入冷却"
        return f"[回执] learned={learned} | {self._short(fields)}"

    def _fmt_fixed_process_eval(self, fields: dict[str, Any]) -> str:
        return f"[处理] 当前站 {fields.get('station')} 需要 {fields.get('processType')}，准备提交 {fields.get('action')}"

    def _fmt_fixed_process_skip(self, fields: dict[str, Any]) -> str:
        return f"[处理] 跳过 {fields.get('station')} 的 {fields.get('processType')}，原因={fields.get('reason')}"

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

    def _fmt_squad_eval_skip(self, fields: dict[str, Any]) -> str:
        return f"[小队] 跳过探路目标 {fields.get('target')}，原因={fields.get('reason')}"

    def _fmt_route_decision(self, fields: dict[str, Any]) -> str:
        if fields.get("nextHop") is None:
            return f"[岔路] 从 {fields.get('fromNode')} 找不到去 {fields.get('target')} 的路，原地保守"
        return (
            f"[岔路] 规划目标={fields.get('target')}，下一跳={fields.get('nextHop')}。"
            "注意：这是路线计划，不代表已移动；是否真的起步看后面的 [发令]/[回执]/下一帧状态。"
        )

    def _fmt_blocker_decision(self, fields: dict[str, Any]) -> str:
        return (
            f"[拦路] {fields.get('target')} 有 {fields.get('blocker')}，"
            f"处理={fields.get('action')} {('任务=' + str(fields.get('taskId'))) if fields.get('taskId') else ''}"
        )

    def _fmt_move_decision(self, fields: dict[str, Any]) -> str:
        return f"[行军] 准备发送 MOVE->{fields.get('target')}；下一帧变 MOVING 或位置变化才算真的动起来"

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
