from __future__ import annotations

from typing import Any

ACTION_FIELDS = [
    "frameCount",
    "submittedActionCount",
    "emptyActionCount",
    "idleEmptyCount",
    "legalSystemWaitCount",
    "waitCount",
    "moveCount",
    "processCount",
    "claimTaskCount",
    "claimResourceCount",
    "useResourceCount",
    "useIceBoxCount",
    "useHorseCount",
    "useIntelCount",
    "windowCardCount",
    "abstainCount",
    "highValueAbstainCount",
    "lowValueAbstainCount",
    "nonAbstainWindowCount",
    "squadScoutCount",
    "squadClearCount",
    "squadWeakenCount",
    "squadReinforceCount",
    "rushSpeedCount",
    "rushProtectCount",
    "verifyGateCount",
    "deliverCount",
    "iceBoxUnusedLowFreshnessFrames",
    "horseUnusedWhileMovingFrames",
    "intelUnusedBeforeGateFrames",
]

BUSY_OR_TRANSIT_STATES = {"MOVING", "PROCESSING", "VERIFYING", "CONTESTING", "RESTING", "FORCED_PASSING", "WAITING"}
HIGH_VALUE_CONTEST_TYPES = {"GATE", "TASK", "PASS", "PROCESS", "FIXED_PROCESS", "VERIFY_GATE", "RESOURCE"}
HIGH_VALUE_RESOURCES = {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL", "PASS_TOKEN", "OFFICIAL_PERMIT"}


def new_audit() -> dict[str, int]:
    return {key: 0 for key in ACTION_FIELDS}


def player_payload(inquire: dict[str, Any], player_id: str) -> dict[str, Any]:
    data = inquire.get("msg_data", {}) if isinstance(inquire.get("msg_data"), dict) else {}
    for player in data.get("players", []) or []:
        if isinstance(player, dict) and str(player.get("playerId")) == str(player_id):
            return player
    return {}


def contests_by_id(inquire: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data = inquire.get("msg_data", {}) if isinstance(inquire.get("msg_data"), dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for contest in data.get("contests", []) or []:
        if isinstance(contest, dict) and contest.get("contestId") is not None:
            result[str(contest.get("contestId"))] = contest
    return result


def is_high_value_window(inquire: dict[str, Any], action: dict[str, Any]) -> bool:
    contest_id = str(action.get("contestId") or "")
    contest = contests_by_id(inquire).get(contest_id, {})
    data = inquire.get("msg_data", {}) if isinstance(inquire.get("msg_data"), dict) else {}
    roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
    gate = roles.get("gateNodeId") or "S14"
    terminals = set(roles.get("terminalNodeIds") or ["S15"])
    target = contest.get("targetNodeId") or action.get("targetNodeId")
    ctype = str(contest.get("contestType") or "").upper()
    resource = str(contest.get("resourceType") or "").upper()
    return ctype in HIGH_VALUE_CONTEST_TYPES or resource in HIGH_VALUE_RESOURCES or bool(contest.get("taskId")) or target == gate or target in terminals


def resource_count(player: dict[str, Any], resource_type: str) -> int:
    resources = player.get("resources") if isinstance(player.get("resources"), dict) else {}
    try:
        return int(resources.get(resource_type, 0) or 0)
    except Exception:
        return 0


def status_of(player: dict[str, Any]) -> str:
    return str(player.get("state") or player.get("status") or "UNKNOWN").upper()


def freshness_of(player: dict[str, Any]) -> float:
    try:
        return float(player.get("freshness", 0) or 0)
    except Exception:
        return 0.0


def task_score_of(player: dict[str, Any]) -> int:
    try:
        return int(player.get("taskScore", 0) or 0)
    except Exception:
        return 0


def has_action(actions: list[dict[str, Any]], action_name: str, resource_type: str | None = None) -> bool:
    for action in actions:
        if not isinstance(action, dict):
            continue
        if str(action.get("action")) != action_name:
            continue
        if resource_type is not None and str(action.get("resourceType")) != resource_type:
            continue
        return True
    return False


def audit_frame(audit: dict[str, int], inquire: dict[str, Any], player_id: str, actions: list[dict[str, Any]]) -> None:
    player = player_payload(inquire, player_id)
    audit["frameCount"] += 1
    audit["submittedActionCount"] += len(actions)
    status = status_of(player)
    legal_wait = status in BUSY_OR_TRANSIT_STATES and (status != "WAITING" or bool(player.get("routeEdgeId") or player.get("currentProcess")))
    if not actions:
        audit["emptyActionCount"] += 1
        if legal_wait:
            audit["legalSystemWaitCount"] += 1
        else:
            audit["idleEmptyCount"] += 1
    for action in actions:
        if not isinstance(action, dict):
            continue
        name = str(action.get("action") or "")
        if name == "WAIT": audit["waitCount"] += 1
        elif name == "MOVE": audit["moveCount"] += 1
        elif name == "PROCESS": audit["processCount"] += 1
        elif name == "CLAIM_TASK": audit["claimTaskCount"] += 1
        elif name == "CLAIM_RESOURCE": audit["claimResourceCount"] += 1
        elif name == "USE_RESOURCE":
            audit["useResourceCount"] += 1
            resource = str(action.get("resourceType") or "")
            if resource == "ICE_BOX": audit["useIceBoxCount"] += 1
            elif resource in {"FAST_HORSE", "SHORT_HORSE"}: audit["useHorseCount"] += 1
            elif resource == "INTEL": audit["useIntelCount"] += 1
        elif name == "WINDOW_CARD":
            audit["windowCardCount"] += 1
            if str(action.get("card")) == "ABSTAIN":
                audit["abstainCount"] += 1
                if is_high_value_window(inquire, action): audit["highValueAbstainCount"] += 1
                else: audit["lowValueAbstainCount"] += 1
            else:
                audit["nonAbstainWindowCount"] += 1
        elif name == "SQUAD_SCOUT": audit["squadScoutCount"] += 1
        elif name == "SQUAD_CLEAR": audit["squadClearCount"] += 1
        elif name == "SQUAD_WEAKEN": audit["squadWeakenCount"] += 1
        elif name == "SQUAD_REINFORCE": audit["squadReinforceCount"] += 1
        elif name == "RUSH_SPEED": audit["rushSpeedCount"] += 1
        elif name == "RUSH_PROTECT": audit["rushProtectCount"] += 1
        elif name == "VERIFY_GATE": audit["verifyGateCount"] += 1
        elif name == "DELIVER": audit["deliverCount"] += 1
    if resource_count(player, "ICE_BOX") > 0 and freshness_of(player) <= 88 and not has_action(actions, "USE_RESOURCE", "ICE_BOX"):
        audit["iceBoxUnusedLowFreshnessFrames"] += 1
    if status in {"MOVING", "WAITING"} and (resource_count(player, "FAST_HORSE") > 0 or resource_count(player, "SHORT_HORSE") > 0) and not (has_action(actions, "USE_RESOURCE", "FAST_HORSE") or has_action(actions, "USE_RESOURCE", "SHORT_HORSE")):
        audit["horseUnusedWhileMovingFrames"] += 1
    if resource_count(player, "INTEL") > 0 and task_score_of(player) >= 90 and not player.get("verified") and not has_action(actions, "USE_RESOURCE", "INTEL"):
        audit["intelUnusedBeforeGateFrames"] += 1
