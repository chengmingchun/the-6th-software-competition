# 一骑红尘：荔枝争运战 Python Baseline

这是一个面向《一骑红尘：荔枝争运战》的基础参赛客户端。目标不是一次写满所有博弈策略，而是先提供一个稳定、可解释、可继续迭代的框架：

- 按官方 TCP 协议接入比赛服务端。
- 每帧可靠回包，避免失联退赛。
- 本地解析地图、状态、任务、资源、窗口和事件。
- 使用动态路线规划和保守策略完成验核、交付、顺路任务与资源获取。
- 代码按层拆分，方便后续增强抢资源、设卡、窗口博弈和小分队策略。

## 文件结构

```text
.
├── start.sh
├── start.bat
├── main.py
├── fixtures/
│   └── minimal_start_inquire.jsonl
├── lizhi_agent/
│   ├── actions.py        # 官方 actions[] 动作结构
│   ├── config.py         # 策略阈值和资源优先级
│   ├── logger.py         # 安全日志，不影响比赛主循环
│   ├── models.py         # start/inquire 状态模型与解析
│   ├── protocol.py       # 5 位长度前缀 TCP 协议
│   ├── route_planner.py  # 加权图搜索与路线耗时估算
│   ├── strategy.py       # 分层基础策略
│   └── utils.py
└── tests/
    └── test_strategy.py
```

## 启动

平台会按以下格式启动：

```bash
./start.sh <playerId> <host> <port>
```

Windows 本地调试可以使用：

```bat
start.bat <playerId> <host> <port>
```

例如连接本地调试服务端：

```bat
start.bat 2779 127.0.0.1 30000
```

也可以直接双击 `start.bat`，进入菜单：

1. 连接本地调试服务端：默认 `2779 / 127.0.0.1 / 30000`。
2. 手动输入 `playerId / host / port` 连接。
3. 跑本地 fixture，不连服务端，只验证 `start -> ready -> inquire -> action`。
4. 跑单元测试。

双击菜单模式会默认开启：

```bat
LIZHI_DEBUG=1
LIZHI_RAW_LOG=1
LIZHI_FILE_LOG=1
LIZHI_VERSION=1.0
```

日志会输出到窗口，也会写入：

```text
logs/<playerId>.jsonl
```

本地可以运行单元测试：

```bash
python -m unittest
```

也可以用 JSON Lines 走本地调试入口：

```bash
python main.py 1001 < fixtures/minimal_start_inquire.jsonl
```

正式比赛时必须传入 `host` 和 `port`，客户端会使用官方格式：

```text
5 位十进制长度前缀 + UTF-8 JSON body
```

## 当前基础策略

每帧按以下优先级决策：

1. 如果有本方参与的窗口争夺，提交一张窗口牌。
2. 如果已交付、退赛、移动中或读条中，发送安全心跳，不打断当前过程。
3. 鲜度过低且有冰鉴时，优先使用 `ICE_BOX`。
4. 在 S15 且已验核时提交 `DELIVER`。
5. 在 S14 且进入 `RUSH` 阶段时提交 `VERIFY_GATE`，可绑定 `BREAK_ORDER`。
6. 当前站点有固定处理流程时，提交 `PROCESS` 或 `DOCK`。
7. 当前站点有高价值任务时，提交 `CLAIM_TASK`，优先拿到 90 分普通任务门槛。
8. 当前站点有高价值资源时，提交 `CLAIM_RESOURCE`。
9. 在不危及交付的前提下，轻微绕路拿 30 分任务或关键资源。
10. 否则按加权最短路前往 S14/S15 完成交付。

## 状态机说明

当前策略是“状态机守卫 + 优先级调度”。

状态机守卫先按主车队状态决定是否允许主动规划：

| 状态类 | 原始状态 | 策略行为 |
|---|---|---|
| `TERMINAL_GUARD` | `DELIVERED` / `RETIRED` | 只发空动作心跳，不再主动操作 |
| `MOVING_GUARD` | `MOVING` / `WAITING` | 默认发空动作心跳让系统继续推进；若没有移动 buff 且有马类资源，会尝试使用马 |
| `BUSY_GUARD` | `PROCESSING` / `VERIFYING` / `RESTING` / `FORCED_PASSING` / `CONTESTING` | 不打断读条、休整或强制通行，只发空动作心跳 |
| `PLANNING` | `IDLE` / `UNKNOWN` / `COST_BANKRUPT` | 进入完整策略调度，评估交付、处理、任务、资源和路线 |

进入 `PLANNING` 后，再按“交付优先、90 分任务门槛、顺路资源、终局保护”的优先级选择动作。

## 日志说明

默认会向 `stderr` 输出 JSON Lines 日志，便于从比赛日志里复盘。可以用环境变量控制：

```bash
LIZHI_DEBUG=1       # 默认开启 stderr 日志
LIZHI_DEBUG=0       # 关闭策略日志
LIZHI_FILE_LOG=1    # 同时写入 logs/<playerId>.jsonl
LIZHI_RAW_LOG=1     # 默认开启，记录 start/inquire/ready/action 的截断预览
LIZHI_RAW_LOG=0     # 只记录摘要，不打印原始 payload 预览
LIZHI_PLAYER_NAME=你的队伍名  # 覆盖 registration.playerName
LIZHI_VERSION=1.0   # 覆盖 registration.version
```

推荐排障启动方式：

```bash
LIZHI_DEBUG=1 LIZHI_RAW_LOG=1 LIZHI_FILE_LOG=1 ./start.sh 2779 127.0.0.1 30000
```

如果看到 `start` 后立刻 `server_closed`，重点检查 `send_message` 里 `msgName=ready` 的 `msgData`、`bodyBytes`、`payloadPreview`，以及 `server_closed` 里的 `sentReady/startReceived/lastRound`。

关键日志事件：

| event | 含义 |
|---|---|
| `connect` | Socket 连接目标、玩家 ID 和连接模式 |
| `send_message` | 发给服务端的完整消息摘要，包含 `msgData`、`bodyBytes`、`frameBytes`、`payloadPreview` |
| `recv_message` | 收到服务端消息，记录 keys、round、phase、任务数、窗口数、事件数、payload 预览 |
| `start_detail` | start 包里的 matchId、duration、players、nodes、edges、roles、gameplay keys |
| `inquire_detail` | 每轮我方 player 状态、tasks、contests、events、actionResults |
| `handle_message` | 当前消息进入哪个协议分支处理 |
| `server_closed` | 服务端断开连接时的上下文，判断是否死在 ready/action 后 |
| `state_snapshot` | 每帧状态机快照，包含位置、状态类、分数、资源、鲜度、任务数、窗口数和到门/终点估计成本 |
| `strategy_step` | 策略命中的关键分支，例如窗口出牌或终局保护 |
| `task_eval_station` | 当前站点可处理任务候选及排序 |
| `resource_eval_station` | 当前站点可领取资源候选及优先级 |
| `task_eval_reachable` | 可绕路任务候选、估值和最终选择 |
| `resource_eval_reachable` | 可绕路资源候选、估值和最终选择 |
| `route_decision` | 目标点和下一跳 |
| `blocker_decision` | 遇到障碍或敌方设卡时选择 T04、CLEAR、BREAK_GUARD 或 FORCED_PASS |
| `squad_eval` | 小分队探路目标选择 |
| `decision` | 本帧最终动作和最终原因 |

## 设计参考

框架参考了 RTS bot 的常见分层：

- 类似 `WorkerData`：`models.py` 只做状态记账和安全读取。
- 类似 `WorkerManager`：`strategy.py` 每帧调度一个合法动作。
- 类似资源点/扩张点管理器：`route_planner.py` 维护地图图和路线成本。

这种拆分能让后续策略增强集中在策略层，而不反复改协议和解析代码。

## 后续增强方向

- 更精确的天气预测与路线耗时模拟。
- 对任务做滚动收益评估，动态选择 60/90/110 分节点。
- 为资源窗口、任务窗口、宫门窗口分别训练或配置出牌策略。
- 引入对手路线预测，在 S10/S11/S14 等关键节点做设卡/削弱。
- 用小分队提前探路关键处理点，或清除必经障碍。
- 增加 replay 日志，离线复盘每帧决策原因。
