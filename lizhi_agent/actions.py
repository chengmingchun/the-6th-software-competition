from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MainActionType(str, Enum):
    WAIT = "WAIT"
    MOVE = "MOVE"
    PROCESS = "PROCESS"
    DOCK = "DOCK"
    CLAIM_RESOURCE = "CLAIM_RESOURCE"
    USE_RESOURCE = "USE_RESOURCE"
    CLAIM_TASK = "CLAIM_TASK"
    CLEAR = "CLEAR"
    SET_GUARD = "SET_GUARD"
    BREAK_GUARD = "BREAK_GUARD"
    FORCED_PASS = "FORCED_PASS"
    VERIFY_GATE = "VERIFY_GATE"
    DELIVER = "DELIVER"


class SquadActionType(str, Enum):
    SQUAD_SCOUT = "SQUAD_SCOUT"
    SQUAD_CLEAR = "SQUAD_CLEAR"
    SQUAD_REINFORCE = "SQUAD_REINFORCE"
    SQUAD_WEAKEN = "SQUAD_WEAKEN"


class WindowCard(str, Enum):
    YAN_DIE = "YAN_DIE"
    QIANG_XING = "QIANG_XING"
    XIAN_GONG = "XIAN_GONG"
    BING_ZHENG = "BING_ZHENG"
    ABSTAIN = "ABSTAIN"


class RushActionType(str, Enum):
    RUSH_SPEED = "RUSH_SPEED"
    RUSH_PROTECT = "RUSH_PROTECT"
    BREAK_ORDER = "BREAK_ORDER"  # only sent as rushTactic on VERIFY_GATE/BREAK_GUARD


@dataclass(frozen=True)
class MainAction:
    action: MainActionType
    target: str | None = None
    task_id: str | None = None
    resource_type: str | None = None
    rush_tactic: RushActionType | None = None
    extra_good_fruit: int | None = None
    good_fruit: int | None = None
    bad_fruit: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_protocol(self) -> dict[str, Any]:
        data: dict[str, Any] = {"action": self.action.value}
        if self.target is not None:
            data["targetNodeId"] = self.target
        if self.task_id is not None:
            data["taskId"] = self.task_id
        if self.resource_type is not None:
            data["resourceType"] = self.resource_type
        if self.rush_tactic is not None:
            data["rushTactic"] = self.rush_tactic.value
        if self.extra_good_fruit is not None:
            data["extraGoodFruit"] = self.extra_good_fruit
        if self.good_fruit is not None:
            data["goodFruit"] = self.good_fruit
        if self.bad_fruit is not None:
            data["badFruit"] = self.bad_fruit
        data.update(self.extra)
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.to_protocol()


@dataclass(frozen=True)
class SquadAction:
    action: SquadActionType
    target: str

    def to_protocol(self) -> dict[str, Any]:
        return {"action": self.action.value, "targetNodeId": self.target}

    def to_dict(self) -> dict[str, Any]:
        return self.to_protocol()


@dataclass(frozen=True)
class WindowAction:
    contest_id: str
    card: WindowCard
    rush_tactic: RushActionType | None = None

    def to_protocol(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": "WINDOW_CARD",
            "contestId": self.contest_id,
            "card": self.card.value,
        }
        if self.rush_tactic is not None:
            data["rushTactic"] = self.rush_tactic.value
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.to_protocol()


@dataclass(frozen=True)
class RushAction:
    action: RushActionType

    def to_protocol(self) -> dict[str, Any]:
        if self.action == RushActionType.BREAK_ORDER:
            raise ValueError("BREAK_ORDER must be bound as rushTactic, not sent as a standalone action")
        return {"action": self.action.value}

    def to_dict(self) -> dict[str, Any]:
        return self.to_protocol()


@dataclass(frozen=True)
class ActionBundle:
    main: MainAction | None = None
    squad: SquadAction | None = None
    window: WindowAction | None = None
    rush: RushAction | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    def to_actions(self) -> list[dict[str, Any]]:
        """Official protocol shape: a flat actions[] array.

        The server enforces per-category limits. Keep at most one action from
        each category in this bundle.
        """
        actions: list[dict[str, Any]] = []
        if self.main is not None:
            actions.append(self.main.to_protocol())
        if self.squad is not None:
            actions.append(self.squad.to_protocol())
        if self.window is not None:
            actions.append(self.window.to_protocol())
        if self.rush is not None:
            actions.append(self.rush.to_protocol())
        return actions

    def to_dict(self) -> dict[str, Any]:
        return {"actions": self.to_actions(), "debug": self.debug}


def wait(reason: str = "fallback", active_wait: bool = True) -> ActionBundle:
    if not active_wait:
        return ActionBundle(debug={"reason": reason})
    return ActionBundle(
        main=MainAction(MainActionType.WAIT),
        debug={"reason": reason},
    )
