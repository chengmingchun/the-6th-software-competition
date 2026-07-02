from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MainActionType(str, Enum):
    WAIT = "WAIT"
    MOVE = "MOVE"
    PROCESS = "PROCESS"
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
    BREAK_ORDER = "BREAK_ORDER"


@dataclass(frozen=True)
class MainAction:
    action: MainActionType
    target: str | None = None
    task_id: str | None = None
    resource_type: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"action": self.action.value}
        if self.target is not None:
            data["target"] = self.target
        if self.task_id is not None:
            data["taskId"] = self.task_id
        if self.resource_type is not None:
            data["resourceType"] = self.resource_type
        data.update(self.extra)
        return data


@dataclass(frozen=True)
class SquadAction:
    action: SquadActionType
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action.value, "target": self.target}


@dataclass(frozen=True)
class WindowAction:
    window_id: str
    card: WindowCard

    def to_dict(self) -> dict[str, Any]:
        return {"windowId": self.window_id, "card": self.card.value}


@dataclass(frozen=True)
class RushAction:
    action: RushActionType

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action.value}


@dataclass(frozen=True)
class ActionBundle:
    main: MainAction | None = None
    squad: SquadAction | None = None
    window: WindowAction | None = None
    rush: RushAction | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a flexible JSON payload.

        The official protocol may use slightly different field names. Keep all
        action categories separated so protocol.py can adapt this shape later.
        """
        payload: dict[str, Any] = {}
        if self.main is not None:
            payload["mainAction"] = self.main.to_dict()
        if self.squad is not None:
            payload["squadAction"] = self.squad.to_dict()
        if self.window is not None:
            payload["windowAction"] = self.window.to_dict()
        if self.rush is not None:
            payload["rushAction"] = self.rush.to_dict()
        if self.debug:
            payload["debug"] = self.debug
        return payload


def wait(reason: str = "fallback") -> ActionBundle:
    return ActionBundle(
        main=MainAction(MainActionType.WAIT),
        debug={"reason": reason},
    )
