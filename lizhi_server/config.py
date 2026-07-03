"""Map configuration and game constants for the local server simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Route coefficients (from 任务书 2.3.2) ──
ROUTE_COEFFICIENT: dict[str, int] = {
    "ROAD": 1380,
    "WATER": 1250,
    "MOUNTAIN": 1780,
    "BRANCH": 1550,
}

# ── Terrain freshness decay per frame (from 任务书 3.2.2) ──
FRESHNESS_DECAY: dict[str, float] = {
    "STATIONARY": 0.05,  # stopped, waiting, processing, verifying, contesting, resting, forced-pass extra
    "ROAD": 0.055,
    "WATER": 0.045,
    "MOUNTAIN": 0.07,
    "BRANCH": 0.065,
}

# ── Horse speeds (from 任务书 2.3.2) ──
BASE_SPEED = 1000
FAST_HORSE_SPEED = 1200
SHORT_HORSE_SPEED = 1150
RUSH_SPEED_BOOST = 1300

# ── Weather multipliers (from 任务书 2.3.2) ──
WEATHER_MULTIPLIER: dict[str, int] = {
    "NONE": 1000,
    "HOT": 1000,  # hot affects freshness, not movement
    "HEAVY_RAIN": 1350,  # only when on WATER route
    "MOUNTAIN_FOG": 1100,  # only when on MOUNTAIN route
}

HEAVY_RAIN_PROCESS_EXTRA = 4  # extra frames for BOARD/WATER_TRANSFER
MOUNTAIN_FOG_SCOUT_DELAY = 2  # extra frames for SQUAD_SCOUT
HOT_FRESHNESS_MULTIPLIER = 1.5
HEAVY_RAIN_FRESHNESS_MULTIPLIER = 1.3
RUSH_SPEED_FRESHNESS_MULTIPLIER = 1.25
RUSH_PROTECT_FRESHNESS_MULTIPLIER = 0.2

# ── Weather schedule (from 任务书 2.5) ──
WEATHER_SCHEDULE: list[dict[str, Any]] = [
    {"start_range": (80, 120), "duration": 60, "type": "HEAVY_RAIN", "region": ["WATER"]},
    {"start_range": (200, 240), "duration": 60, "type": "MOUNTAIN_FOG", "region": ["MOUNTAIN"]},
    {"start_range": (320, 360), "duration": 60, "type": "HOT", "region": ["ALL"]},
    {"start_range": (440, 480), "duration": 60, "type": "HEAVY_RAIN", "region": ["WATER"]},
]
WEATHER_PREVIEW_FRAMES = 30

# ── Initial resources (from 任务书 2.4.2 / 协议附录A) ──
INITIAL_RESOURCES: dict[str, dict[str, int]] = {
    "S03": {"ICE_BOX": 1, "PASS_TOKEN": 1, "INTEL": 1},
    "S04": {"SHORT_HORSE": 1, "BOAT_RIGHT": 1, "INTEL": 1},
    "S06": {"ICE_BOX": 1, "INTEL": 1},
    "S07": {"ICE_BOX": 1, "SHORT_HORSE": 1},
    "S08": {"SHORT_HORSE": 1, "PASS_TOKEN": 1, "INTEL": 1},
    "S09": {"FAST_HORSE": 1, "OFFICIAL_PERMIT": 1},
    "S10": {"INTEL": 1},
    "S11": {"INTEL": 1},
    "S13": {"PASS_TOKEN": 1, "OFFICIAL_PERMIT": 1, "INTEL": 1},
}
RESOURCE_CLAIM_FRAMES = 2  # all resources take 2 frames to claim

# ── Fixed process nodes (from 任务书 2.4.1) ──
FIXED_PROCESS_NODES: dict[str, tuple[str, int, bool]] = {
    "S02": ("TRANSFER", 4, True),
    "S04": ("BOARD", 7, True),
    "S05": ("WATER_TRANSFER", 6, True),
    "S11": ("PASS_TRANSFER", 5, True),
    "S13": ("PALACE_TRANSFER", 5, True),
    "S14": ("VERIFY", 6, True),
}

# ── Task templates (from 任务书 5.2) ──
TASK_TEMPLATES: dict[str, tuple[int, int, list[str]]] = {
    "T01": (30, 3, ["S03"]),
    "T02": (30, 4, ["S07", "S10"]),
    "T04": (30, 6, ["S06", "S08"]),  # clears obstacle
    "T06": (30, 3, ["S09", "S04", "S06"]),  # consumes horse
    "T08": (30, 4, ["S04", "S05"]),
    "T11": (30, 4, ["S08", "S10", "S11"]),
    "T12": (15, 5, ["S11", "S13"]),
    "T13": (15, 5, ["S13", "S09", "S12"]),
    "T14": (15, 5, ["S10", "S11", "S12"]),
}

# ── Obstacle candidates (from 任务书 2.4.4) ──
OBSTACLE_CANDIDATES: dict[str, list[str]] = {
    "S06": ["S01", "S03", "S08"],
    "S08": ["S06", "S07", "S09", "S10"],
    "S10": ["S08", "S09", "S11"],
    "S11": ["S10", "S12"],
}

# ── Node definitions ──
NODE_INFO: dict[str, dict[str, Any]] = {
    "S01": {"name": "岭南果园", "x": 4, "y": 30, "type": "START", "code": "101"},
    "S02": {"name": "南岭驿", "x": 11, "y": 27, "type": "CHECKPOINT", "code": "102"},
    "S03": {"name": "梅关驿", "x": 18, "y": 27, "type": "PASS", "code": "103"},
    "S04": {"name": "江南码头", "x": 13, "y": 19, "type": "DOCK", "code": "104"},
    "S05": {"name": "洞庭水驿", "x": 15, "y": 12, "type": "WATER_STATION", "code": "105"},
    "S06": {"name": "五岭山道", "x": 14, "y": 34, "type": "MOUNTAIN_NODE", "code": "106"},
    "S07": {"name": "荆襄大驿", "x": 29, "y": 27, "type": "STATION", "code": "107"},
    "S08": {"name": "秦岭栈道", "x": 32, "y": 36, "type": "MOUNTAIN_PASS", "code": "108"},
    "S09": {"name": "洛阳驿", "x": 41, "y": 19, "type": "STATION", "code": "109"},
    "S10": {"name": "武关", "x": 49, "y": 29, "type": "KEY_PASS", "code": "110"},
    "S11": {"name": "潼关驿", "x": 58, "y": 28, "type": "PASS", "code": "111"},
    "S12": {"name": "关中平原", "x": 63, "y": 22, "type": "JUNCTION", "code": "112"},
    "S13": {"name": "灞桥驿", "x": 69, "y": 20, "type": "PALACE_STATION", "code": "113"},
    "S14": {"name": "朱雀门", "x": 76, "y": 18, "type": "GATE", "code": "114"},
    "S15": {"name": "兴庆宫", "x": 78, "y": 18, "type": "FINISH", "code": "115"},
}

# ── Edge definitions (from user's start packet / Appendix A) ──
EDGE_DEFS: list[dict[str, Any]] = [
    {"id": "E01", "from": "S01", "to": "S02", "type": "ROAD", "dist": 30},
    {"id": "E02", "from": "S02", "to": "S03", "type": "ROAD", "dist": 25},
    {"id": "E03", "from": "S03", "to": "S07", "type": "ROAD", "dist": 54},
    {"id": "E04", "from": "S07", "to": "S09", "type": "ROAD", "dist": 46},
    {"id": "E05", "from": "S09", "to": "S10", "type": "ROAD", "dist": 40},
    {"id": "E06", "from": "S10", "to": "S11", "type": "ROAD", "dist": 36},
    {"id": "E07", "from": "S11", "to": "S12", "type": "ROAD", "dist": 20},
    {"id": "E08", "from": "S12", "to": "S13", "type": "ROAD", "dist": 25},
    {"id": "E09", "from": "S13", "to": "S14", "type": "ROAD", "dist": 18},
    {"id": "E10", "from": "S14", "to": "S15", "type": "ROAD", "dist": 10},
    {"id": "E11", "from": "S02", "to": "S04", "type": "ROAD", "dist": 20},
    {"id": "E12", "from": "S04", "to": "S05", "type": "WATER", "dist": 44},
    {"id": "E13", "from": "S05", "to": "S07", "type": "BRANCH", "dist": 46},
    {"id": "E15", "from": "S01", "to": "S06", "type": "MOUNTAIN", "dist": 44},
    {"id": "E16", "from": "S06", "to": "S08", "type": "MOUNTAIN", "dist": 54},
    {"id": "E17", "from": "S08", "to": "S10", "type": "BRANCH", "dist": 46},
    {"id": "E18", "from": "S03", "to": "S06", "type": "BRANCH", "dist": 38},
    {"id": "E19", "from": "S05", "to": "S09", "type": "WATER", "dist": 48},
    {"id": "E20", "from": "S07", "to": "S08", "type": "MOUNTAIN", "dist": 42},
    {"id": "E21", "from": "S04", "to": "S07", "type": "BRANCH", "dist": 54},
    {"id": "E22", "from": "S08", "to": "S09", "type": "BRANCH", "dist": 64},
]

# ── Guard node defense caps (from 任务书 6.2.1) ──
GUARD_DEFENSE_CAP: dict[str, int] = {
    "default": 6,
    "KEY_PASS": 7,
    "GATE": 4,
}
GUARD_DEFENSE_WITH_OBSTACLE = 5

# ── Guard wind (weathering) intervals (from 任务书 6.2.2) ──
GUARD_WIND_INTERVAL = 30  # frames
GUARD_KEY_PASS_EXTRA_FIRST_WIND = 45  # key pass with defense >=4 gets 45 frames before first wind

# ── Scoring constants (from 任务书 7.2) ──
TASK_SCORE_MILESTONES: list[tuple[int, int]] = [(60, 15), (90, 20), (110, 15)]

# ── Window card win matrix (from 任务书 5.4.4) ──
# result[my_card][opponent_card] = "WIN" / "LOSE" / "DRAW"
WINDOW_MATRIX: dict[str, dict[str, str]] = {
    "YAN_DIE":    {"YAN_DIE": "DRAW", "QIANG_XING": "WIN",  "XIAN_GONG": "LOSE", "BING_ZHENG": "LOSE"},
    "QIANG_XING": {"YAN_DIE": "LOSE", "QIANG_XING": "DRAW", "XIAN_GONG": "WIN",  "BING_ZHENG": "LOSE"},
    "XIAN_GONG":  {"YAN_DIE": "WIN",  "QIANG_XING": "LOSE", "XIAN_GONG": "DRAW", "BING_ZHENG": "WIN"},
    "BING_ZHENG": {"YAN_DIE": "WIN",  "QIANG_XING": "WIN",  "XIAN_GONG": "LOSE", "BING_ZHENG": "DRAW"},
}

# ── Bounty scores (from 任务书 6.3.3) ──
NORMAL_BOUNTY_SCORE = 10
KEY_BOUNTY_SCORE = 18

# ── Game limits ──
MAX_FRAMES = 600
FRESHNESS_MAX = 100.0
GOOD_FRUIT_BAD_THRESHOLDS = [90, 80, 70, 60, 50, 40, 30, 20, 10]
MAX_TASKS_ACTIVE = 18
MAX_TASKS_VISIBLE = 10

# ── Task refresh schedule ──
TASK_REFRESH_INTERVAL = 15  # frames between task refreshes
TASK_MAX_ON_MAP = 12
TASK_EXPIRE_FRAMES = 60  # tasks expire after ~60 frames
