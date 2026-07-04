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
    "guardBlockedMoveResultCount",
    "maxGuardBlockedMoveStreak",
    "iceBoxUnusedLowFreshnessFrames",
    "horseUnusedWhileMovingFrames",
    "intelUnusedBeforeGateFrames",
]

BUSY_OR_TRANSIT_STATES = {"MOVING", "PROCESSING", "VERIFYING", "CONTESTING", "RESTING", "FORCED_PASSING", "WAITING", "DELIVERED", "RETIRED"}
HIGH_VALUE_CONTEST_TYPES = {"GATE", "TASK", "PASS", "PROCESS", "FIXED_PROCESS", "VERIFY_GATE", "RESOURCE"}
HIGH_VALUE_RESOURCES = {"ICE_BOX", "FAST_HORSE", "SHORT_HORSE", "INTEL", "PASS_TOKEN", "OFFICIAL_PERMIT"}


def new_audit() -> dict[str, int]:
    return {key: 0 for key in ACTION_FIELDS}


def _append_detail(audit: dict[str, Any], key: str, detail: dict[str, Any]) -> None:
    details = audit.setdefault(key, [])
    if isinstance(details, list):
        details.append(detail)


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


def phase_of(inquire: dict[str, Any]) -> str:
    data = inquire.get("msg_data", {}) if isinstance(inquire.get("msg_data"), dict) else {}
    return str(data.get("phase") or inquire.get("phase") or "UNKNOWN").upper()


def _payload(inquire: dict[str, Any]) -> dict[str, Any]:
    return inquire.get("msg_data", {}) if isinstance(inquire.get("msg_data"), dict) else {}


def _first_present(record: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in record and record.get(key) not in (None, ""):
            return record.get(key)
    return default


def current_node_of(player: dict[str, Any]) -> str:
    return str(_first_present(player, "currentNodeId", "nodeId", "station", "currentNode", default="") or "")


def _edge_nodes(edge: dict[str, Any]) -> tuple[str, str] | None:
    start = _first_present(edge, "start", "from", "fromNodeId", "source", "sourceNodeId")
    end = _first_present(edge, "end", "to", "toNodeId", "target", "targetNodeId")
    if start in (None, "") or end in (None, ""):
        return None
    return str(start), str(end)


def _edge_distance(edge: dict[str, Any]) -> int:
    try:
        return max(1, int(_first_present(edge, "distance", "length", "dist", default=1) or 1))
    except Exception:
        return 1


def _edges(inquire: dict[str, Any]) -> list[dict[str, Any]]:
    data = _payload(inquire)
    candidates = data.get("edges")
    if not candidates and isinstance(data.get("map"), dict):
        candidates = data["map"].get("edges")
    return [edge for edge in (candidates or []) if isinstance(edge, dict)]


def _route_distance(inquire: dict[str, Any], start: str, target: str) -> int | None:
    if not start or not target:
        return None
    if start == target:
        return 0
    graph: dict[str, list[tuple[str, int]]] = {}
    for edge in _edges(inquire):
        nodes = _edge_nodes(edge)
        if nodes is None:
            continue
        a, b = nodes
        distance = _edge_distance(edge)
        graph.setdefault(a, []).append((b, distance))
        graph.setdefault(b, []).append((a, distance))
    if not graph:
        return None
    queue: list[tuple[int, str]] = [(0, start)]
    seen: dict[str, int] = {start: 0}
    while queue:
        queue.sort(reverse=True)
        cost, node = queue.pop()
        if node == target:
            return cost
        if cost != seen.get(node):
            continue
        for neighbor, distance in graph.get(node, []):
            next_cost = cost + distance
            if next_cost >= seen.get(neighbor, 10**9):
                continue
            seen[neighbor] = next_cost
            queue.append((next_cost, neighbor))
    return None


def intel_actionable_target_exists(inquire: dict[str, Any], player: dict[str, Any]) -> bool:
    """INTEL is actionable only when a useful target is legal by route distance."""
    start = current_node_of(player)
    data = _payload(inquire)
    roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
    gate = str(roles.get("gateNodeId") or "S14")
    for target in ("S10", "S11", "S13"):
        if target == start:
            continue
        distance = _route_distance(inquire, start, target)
        if distance is not None and distance <= 15:
            return True
    if player.get("verified"):
        return False
    gate_distance = _route_distance(inquire, start, gate)
    if gate_distance is None or gate_distance > 15:
        return False
    if start == gate or start == "S13" or phase_of(inquire) in {"RUSH", "ENDGAME", "FINAL"}:
        return True
    return False


def resource_actionable_status(player: dict[str, Any]) -> bool:
    """Return whether a non-horse resource could reasonably be used now."""
    status = status_of(player)
    if status not in {"IDLE", "WAITING"}:
        return False
    if player.get("routeEdgeId") or player.get("targetNodeId") or player.get("target"):
        return False
    return not bool(player.get("currentProcess"))


def should_warn_ice_box_unused(inquire: dict[str, Any], player: dict[str, Any]) -> bool:
    """Flag held ICE_BOX only when protocol-actionable and strategically urgent."""
    freshness = freshness_of(player)
    if freshness > 88:
        return False
    if freshness <= 82:
        return True
    if task_score_of(player) >= 90:
        return True
    if phase_of(inquire) in {"RUSH", "ENDGAME", "FINAL"}:
        return True
    return bool(player.get("verified") or player.get("delivered"))


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


def _record_belongs_to_player(record: dict[str, Any], player_id: str) -> bool:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    values = [record.get("playerId"), record.get("actorPlayerId"), payload.get("playerId"), payload.get("actorPlayerId")]
    explicit = [value for value in values if value not in (None, "")]
    if not explicit:
        return True
    return any(str(value) == str(player_id) for value in explicit)


def _result_code(result: dict[str, Any]) -> str:
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return str(result.get("code") or result.get("errorCode") or result.get("reason") or result.get("message") or payload.get("code") or payload.get("errorCode") or "").upper()


def _result_action(result: dict[str, Any]) -> str:
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return str(result.get("action") or result.get("actionType") or result.get("type") or payload.get("action") or payload.get("actionType") or "").upper()


def _result_target(result: dict[str, Any]) -> str:
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return str(
        result.get("targetNodeId")
        or result.get("nextNodeId")
        or result.get("nodeId")
        or result.get("node")
        or payload.get("targetNodeId")
        or payload.get("nextNodeId")
        or payload.get("nodeId")
        or payload.get("node")
        or "UNKNOWN"
    )


def audit_action_results(audit: dict[str, Any], inquire: dict[str, Any], player_id: str) -> None:
    data = inquire.get("msg_data", {}) if isinstance(inquire.get("msg_data"), dict) else {}
    guard_block_targets: list[str] = []
    for result in data.get("actionResults", []) or []:
        if not isinstance(result, dict) or not _record_belongs_to_player(result, player_id):
            continue
        if _result_code(result) != "MOVE_BLOCKED_BY_GUARD":
            continue
        action = _result_action(result)
        if action and action != "MOVE":
            continue
        target = _result_target(result)
        guard_block_targets.append(target)
        audit["guardBlockedMoveResultCount"] += 1
    if not guard_block_targets:
        audit["_guardBlockTarget"] = ""
        audit["_guardBlockStreak"] = 0
        return
    target = guard_block_targets[-1]
    if audit.get("_guardBlockTarget") == target:
        streak = int(audit.get("_guardBlockStreak", 0) or 0) + 1
    else:
        streak = 1
    audit["_guardBlockTarget"] = target
    audit["_guardBlockStreak"] = streak
    audit["maxGuardBlockedMoveStreak"] = max(int(audit.get("maxGuardBlockedMoveStreak", 0) or 0), streak)


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
    resource_actionable = resource_actionable_status(player)
    if resource_actionable and resource_count(player, "ICE_BOX") > 0 and should_warn_ice_box_unused(inquire, player) and not has_action(actions, "USE_RESOURCE", "ICE_BOX"):
        audit["iceBoxUnusedLowFreshnessFrames"] += 1
        _append_detail(
            audit,
            "_iceBoxUnusedDetails",
            {
                "frame": _payload(inquire).get("round") or _payload(inquire).get("frame") or inquire.get("round"),
                "status": status,
                "station": current_node_of(player),
                "target": player.get("targetNodeId") or player.get("target"),
                "routeEdgeId": player.get("routeEdgeId"),
                "freshness": freshness_of(player),
                "taskScore": task_score_of(player),
                "actions": [action.get("action") for action in actions if isinstance(action, dict)],
            },
        )
    # Latest online behavior treats MOVING as a hard no-command state. Do not
    # pressure strategy into using horse buffs mid-edge; only flag missed horse
    # timing while stopped/paused and still able to choose a safe action.
    if status == "WAITING" and (resource_count(player, "FAST_HORSE") > 0 or resource_count(player, "SHORT_HORSE") > 0) and not (has_action(actions, "USE_RESOURCE", "FAST_HORSE") or has_action(actions, "USE_RESOURCE", "SHORT_HORSE")):
        audit["horseUnusedWhileMovingFrames"] += 1
    if resource_actionable and resource_count(player, "INTEL") > 0 and task_score_of(player) >= 90 and not player.get("verified") and intel_actionable_target_exists(inquire, player) and not has_action(actions, "USE_RESOURCE", "INTEL"):
        audit["intelUnusedBeforeGateFrames"] += 1
        _append_detail(
            audit,
            "_intelUnusedDetails",
            {
                "frame": _payload(inquire).get("round") or _payload(inquire).get("frame") or inquire.get("round"),
                "status": status,
                "station": current_node_of(player),
                "target": player.get("targetNodeId") or player.get("target"),
                "routeEdgeId": player.get("routeEdgeId"),
                "freshness": freshness_of(player),
                "taskScore": task_score_of(player),
                "actions": [action.get("action") for action in actions if isinstance(action, dict)],
            },
        )
    audit_action_results(audit, inquire, player_id)
