# 一骑红尘：荔枝争运战 Baseline

这是一个面向《一骑红尘：荔枝争运战》的 Python 标准库 baseline。目标不是一次写满所有策略，而是先提供一个稳定、可扩展、可调试的比赛客户端骨架。

## 设计目标

1. 先保证客户端能启动、连接、读状态、输出动作。
2. 主策略优先做到：不退赛、少非法、能走主线、能验核、能交付。
3. 代码保持模块化，后续方便让 Codex 分模块增强。
4. 不依赖第三方库，避免比赛环境现场安装依赖。

## 通信协议状态

当前已按官方通信协议接入：

```text
TCP Socket
5 位十进制长度前缀 + UTF-8 JSON body
registration -> start -> ready -> inquire/action -> over
```

客户端连接后会自动发送 `registration`，收到 `start` 后缓存 `matchId / nodes / edges / resources / taskTemplates`，随后发送 `ready`。每次收到 `inquire.round=N` 后，会发送 `action.round=N`。

动作输出为官方平铺格式：

```json
{
  "msg_name": "action",
  "msg_data": {
    "matchId": "match_001",
    "round": 12,
    "playerId": 1001,
    "actions": [
      {"action": "MOVE", "targetNodeId": "S03"}
    ]
  }
}
```

## 文件结构

```text
.
├── start.sh
├── main.py
├── lizhi_agent/
│   ├── __init__.py
│   ├── actions.py
│   ├── config.py
│   ├── logger.py
│   ├── models.py
│   ├── protocol.py
│   ├── route_planner.py
│   ├── strategy.py
│   └── utils.py
└── tests/
    └── test_strategy.py
```

## 启动方式

平台通常会传入：

```bash
./start.sh <playerId> <host> <port>
```

本地也可以用标准输入 JSON Lines 调试：

```bash
python3 main.py 1001
```

本地跑测试：

```bash
python3 -m unittest
```

## 当前 baseline 策略

每帧按顺序判断：

1. 若有窗口，提交 `WINDOW_CARD`。
2. 若已交付，发送空动作心跳。
3. 若在 S15 且满足交付条件，提交 `DELIVER`。
4. 若在 S14 且可验核，提交 `VERIFY_GATE`。
5. 若当前位置需要固定处理，提交 `PROCESS`。
6. 若当前位置有高价值任务，且任务分未到 90，提交 `CLAIM_TASK`。
7. 若当前位置有优先资源，提交 `CLAIM_RESOURCE`。
8. 否则按路线向 S14 / S15 移动。
9. 无法判断时提交 `WAIT` 或空动作心跳。

## 后续增强方向

- 用真实地图/样例消息做端到端联调。
- 增强任务收益评估，优先凑够 90 分任务。
- 增强固定处理是否已完成的判断，避免重复 `PROCESS`。
- 增强资源使用策略：冰鉴、马、情报。
- 增强窗口出牌策略。
- 增强小分队探路、清障策略。
- 增强终局急策：疾行令、护果令、破关令。
- 添加 replay 记录和离线复盘。
