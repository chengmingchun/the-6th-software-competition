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
    RUSH_SPEED = "RUSH_SPEED"
    RUSH_PROTECT = "RUSH_PROTECT"


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


@dataclass(frozen=True)
class MainAction:
    """One official main-convoy action.

    The service expects action dictionaries inside msg_data.actions[].  Keep
    this object close to the protocol field names so the strategy can stay
    readable and avoid handwritten JSON fragments.
    """

    action: MainActionType
    target: str | None = None
    task_id: str | None = None
    resource_type: str | None = None
    good_fruit: int | None = None
    bad_fruit: int | None = None
    extra_good_fruit: int | None = None
    rush_tactic: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_action(self) -> dict[str, Any]:
        data: dict[str, Any] = {"action": self.action.value}
        if self.target is not None:
            data["targetNodeId"] = self.target
        if self.task_id is not None:
            data["taskId"] = self.task_id
        if self.resource_type is not None:
            data["resourceType"] = self.resource_type
        if self.good_fruit is not None:
            data["goodFruit"] = self.good_fruit
        if self.bad_fruit is not None:
            data["badFruit"] = self.bad_fruit
        if self.extra_good_fruit is not None:
            data["extraGoodFruit"] = self.extra_good_fruit
        if self.rush_tactic is not None:
            data["rushTactic"] = self.rush_tactic
        data.update(self.extra)
        return data


@dataclass(frozen=True)
class SquadAction:
    action: SquadActionType
    target: str

    def to_action(self) -> dict[str, Any]:
        return {"action": self.action.value, "targetNodeId": self.target}


@dataclass(frozen=True)
class WindowAction:
    contest_id: str
    card: WindowCard

    def to_action(self) -> dict[str, Any]:
        return {
            "action": "WINDOW_CARD",
            "contestId": self.contest_id,
            "card": self.card.value,
        }


@dataclass(frozen=True)
class ActionBundle:
    """Actions to submit in one frame.

    Official limits allow at most one main action, one squad action, and one
    window card in the same frame.  The bundle preserves that shape and emits a
    flat actions[] list for the network client.
    """

    main: MainAction | None = None
    squad: SquadAction | None = None
    window: WindowAction | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    def to_actions(self) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if self.main is not None:
            actions.append(self.main.to_action())
        if self.squad is not None:
            actions.append(self.squad.to_action())
        if self.window is not None:
            actions.append(self.window.to_action())
        return actions

    def to_dict(self) -> dict[str, Any]:
        return {"actions": self.to_actions(), "debug": self.debug}


def wait(reason: str = "fallback", active: bool = True) -> ActionBundle:
    """Return a heartbeat-safe wait.

    active=True sends WAIT, which intentionally pauses a convoy on an edge.
    Use active=False only when the desired behavior is system wait / keep
    moving; the official protocol still receives an action packet with [].
    """

    main = MainAction(MainActionType.WAIT) if active else None
    return ActionBundle(main=main, debug={"reason": reason})
