"""Baseline agent package for 一骑红尘：荔枝争运战."""

from __future__ import annotations

from dataclasses import replace as _replace
from typing import Any as _Any

__all__ = [
    "actions",
    "config",
    "logger",
    "models",
    "protocol",
    "route_planner",
    "strategy",
    "utils",
]


def _first_present_from_result(result: dict[str, _Any], payload: dict[str, _Any], state) -> _Any:
    """Find the real target for blocked-move feedback.

    Some judge packets report a failed move as WAIT + MOVE_BLOCKED_BY_GUARD
    while the convoy is still waiting at the current station.  In that shape,
    nodeId is often the current station, while the intended next node is carried
    by the player's nextNodeId/target field.  Prefer target-like fields before
    falling back to nodeId/current station.
    """

    for key in ("targetNodeId", "nextNodeId", "target"):
        value = result.get(key)
        if value not in (None, ""):
            return value
    for key in ("targetNodeId", "nextNodeId", "target"):
        value = payload.get(key)
        if value not in (None, ""):
            return value
    if state.me.target not in (None, ""):
        return state.me.target
    for key in ("targetNodeId", "nextNodeId", "target"):
        value = state.me.raw.get(key) if isinstance(state.me.raw, dict) else None
        if value not in (None, ""):
            return value
    return result.get("nodeId") or payload.get("nodeId") or state.me.station


def _patch_models_action_result_normalization() -> None:
    from . import models as _models

    original = _models.parse_game_state
    if getattr(original, "_guard_wait_normalized", False):
        return

    def parse_game_state_with_guard_wait_normalization(player_id: str, start_data: dict[str, _Any], inquire_data: dict[str, _Any]):
        state = original(player_id, start_data, inquire_data)
        normalized: list[dict[str, _Any]] = []
        changed = False
        for item in state.action_results:
            if not isinstance(item, dict):
                continue
            result = dict(item)
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            code = str(
                result.get("code")
                or result.get("errorCode")
                or result.get("reason")
                or result.get("message")
                or payload.get("code")
                or payload.get("errorCode")
                or ""
            ).upper()
            action = str(result.get("action") or result.get("actionType") or result.get("type") or payload.get("action") or payload.get("actionType") or "").upper()
            if code == "MOVE_BLOCKED_BY_GUARD" and action != "MOVE":
                target = _first_present_from_result(result, payload, state)
                result.setdefault("rawAction", action or "UNKNOWN")
                result["action"] = "MOVE"
                if target not in (None, ""):
                    result["targetNodeId"] = str(target)
                result.setdefault("playerId", str(player_id))
                result.setdefault("normalizedFrom", "WAIT_MOVE_BLOCKED_BY_GUARD")
                changed = True
            normalized.append(result)
        if not changed:
            return state
        return _replace(state, action_results=normalized)

    parse_game_state_with_guard_wait_normalization._guard_wait_normalized = True  # type: ignore[attr-defined]
    _models.parse_game_state = parse_game_state_with_guard_wait_normalization


def _patch_strategy_speed_resource_usage() -> None:
    from .actions import ActionBundle, MainAction, MainActionType
    from . import strategy as _strategy

    original = _strategy.BaselineStrategy._pre_move_resource_action
    if getattr(original, "_delivery_speed_patched", False):
        return

    def pre_move_resource_action_with_delivery_speed(self, state):
        me = state.me
        if me.status not in _strategy.PLANNING_STATES or me.station is None:
            return None
        if me.has_buff("FAST_HORSE", "SHORT_HORSE", "RUSH_SPEED"):
            return None
        delivery_remaining = self._remaining_delivery_cost(state)
        should_speed_delivery = me.task_score_base >= self.config.target_task_score or state.turns_left < 420 or self._should_lock_delivery(state)
        if me.has_resource("FAST_HORSE") and should_speed_delivery and delivery_remaining >= 4:
            self.logger.info("resource_use", resourceType="FAST_HORSE", reason="pre_move_delivery_speedup", remainingCost=delivery_remaining, turnsLeft=state.turns_left, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="FAST_HORSE"))
        if me.has_resource("SHORT_HORSE") and should_speed_delivery and delivery_remaining >= 3:
            self.logger.info("resource_use", resourceType="SHORT_HORSE", reason="pre_move_delivery_speedup", remainingCost=delivery_remaining, turnsLeft=state.turns_left, taskScore=me.task_score_base)
            return ActionBundle(main=MainAction(MainActionType.USE_RESOURCE, resource_type="SHORT_HORSE"))
        return original(self, state)

    pre_move_resource_action_with_delivery_speed._delivery_speed_patched = True  # type: ignore[attr-defined]
    _strategy.BaselineStrategy._pre_move_resource_action = pre_move_resource_action_with_delivery_speed


_patch_models_action_result_normalization()
_patch_strategy_speed_resource_usage()
