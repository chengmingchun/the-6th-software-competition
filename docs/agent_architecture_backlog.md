# Game Agent Architecture Backlog

This project is moving from a rule bot to an auditable game-agent system.

## Current principle

Do not blindly add more `if` branches. Every strategy change must improve at
least one measured behavior metric without creating hidden regressions.

Recommended loop:

```bash
python tools/run_audit_pipeline.py --bot-a . --bot-b claude --seeds 1-20 --allow-gate-fail
```

For final candidates:

```bash
python tools/run_audit_pipeline.py --bot-a . --bot-b claude --seeds 1-50 --strict
```

## P0: Observability and regression gate

Status: in progress.

Implemented:

- `tools/audit_metrics.py`
- `tools/audited_tournament_runner.py`
- `tools/audit_report.py`
- `tools/audit_gate.py`
- `tools/run_audit_pipeline.py`

Key metrics:

- `idleEmptyCount`: IDLE but no action. This is usually a bug.
- `legalSystemWaitCount`: legal empty action during move/process/window.
- `highValueAbstainCount`: high-value window but ABSTAIN.
- `useResourceCount`: all resource usage.
- `iceBoxUnusedLowFreshnessFrames`: freshness low, holding ICE_BOX, but not using it.
- `horseUnusedWhileMovingFrames`: moving while holding horse resource.
- `intelUnusedBeforeGateFrames`: task score >= 90, holding INTEL, not verified.
- `rejectedActionCount`: server-side invalid action count.

Next:

- Add replay extraction for the top N worst frames per metric.
- Add per-seed diff table: which seed lost and why.
- Add trend comparison against previous commit or frozen branch.

## P1: Legal Action Oracle

Goal: strategy should not guess legality.

Planned module:

```text
lizhi_agent/legal_actions.py
```

Responsibilities:

- Determine legal main actions under current status.
- Determine whether PROCESS is required before leaving a fixed node.
- Determine legal resource usage under buffs and inventory.
- Determine legal window cards under resource constraints.
- Validate final `ActionBundle` before sending.

Expected metric impact:

- Lower `rejectedActionCount`.
- Lower `idleEmptyCount` caused by invalid target/action fallback.

## P2: Resource Manager

Goal: resources become an economy, not scattered triggers.

Planned module:

```text
lizhi_agent/resource_policy.py
```

Responsibilities:

- Score ICE_BOX by freshness, weather, remaining route, and delivery target.
- Score FAST_HORSE/SHORT_HORSE by remaining distance and buff conflict.
- Score INTEL targets by frames saved: S14 gate, fixed PROCESS, task, resource.
- Explain every resource skip.

Expected metric impact:

- Lower `iceBoxUnusedLowFreshnessFrames`.
- Lower `horseUnusedWhileMovingFrames`.
- Lower `intelUnusedBeforeGateFrames`.
- Higher freshness without killing task score.

## P3: Window EV Policy

Goal: window cards are selected by expected value.

Planned module:

```text
lizhi_agent/window_policy.py
```

Responsibilities:

- Classify window value: gate, task, fixed process, resource, pass, obstacle.
- Estimate card cost: guard point, pass permit, horse, fruit/freshness.
- Estimate win value and opponent pressure.
- Avoid ABSTAIN in high-value windows unless cost is provably too high.
- Explain ABSTAIN reason.

Expected metric impact:

- Lower `highValueAbstainCount`.
- Higher task/resource/gate success.
- Controlled good fruit loss.

## P4: Task and delivery EV

Goal: stop using static thresholds as the main decision driver.

Planned module:

```text
lizhi_agent/utility.py
```

Candidate formulas:

```text
task_ev = task_score_gain - detour_time_cost - freshness_loss_cost - contest_risk + threshold_bonus
resource_ev = future_time_saved + future_freshness_saved - claim_cost - detour_cost
delivery_ev = projected_delivery_score + safety_bonus - opportunity_cost
window_ev = object_value * estimated_win_prob - card_cost
```

Expected metric impact:

- Raise task score when too conservative.
- Raise freshness when too greedy.
- Improve total score stability across seeds.

## P5: Behavior Tree / policy composition

Goal: replace the long priority chain with composable nodes.

Proposed tree:

```text
RootSelector
├── TerminalGuard
├── BusyOrTransitWait
├── WindowPolicy
├── CriticalSurvivalPolicy
├── TerminalDeliveryPolicy
├── GateVerifyPolicy
├── FixedProcessPolicy
├── DeliveryGuardPolicy
├── StationOpportunityPolicy
├── ResourceUsePolicy
├── ReachableTaskPolicy
├── ReachableResourcePolicy
└── DefaultRoutePolicy
```

Expected impact:

- Easier reasoning.
- Fewer priority collisions.
- Better logs: each decision names the winning node.

## P6: Rollout planner

Goal: compare small action plans, not just single actions.

Possible implementation:

```text
ForwardModel.clone()
ForwardModel.apply_plan(actions)
score_projection(state)
```

Plan candidates:

- Deliver directly.
- Claim nearby task then deliver.
- Claim ICE_BOX / horse then deliver.
- Scout S14 then verify.

Expected impact:

- Better route/resource/task tradeoff.
- Less threshold tuning.

## Decision rule for future changes

Every future strategy change must state:

1. Which metric it is intended to improve.
2. Which metric it might hurt.
3. How to test it through `run_audit_pipeline.py`.

If a change improves one seed but hurts audit averages, it is not a final-candidate change.
