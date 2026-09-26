# D5: Budget Enforcement Design

> Status: draft
> **v0.2.0:** extended by [Runtime Guards (D30)](runtime-guards-design.md).

## Problem

Spend discipline is a fleet-level property. Budgets must be enforced during execution, not reported after the fact (DD-04). After the PRD rewrite, the budget service must also provide: cost showback (team/agent spend attribution), waste detection, ROI flags, and cost-per-completed-task (not per-call — real cost including retries, failed loops, and escalations).

See [PRD 02: Architecture](../prd/02-architecture.md) § Budget & Cost Service and [PRD 05: Features](../prd/05-features.md) § Cost & ROI.

## Budget Levels

| Level | Scope | Enforced when |
|-------|-------|---------------|
| Per run | One run | Before each model/tool call and at admission |
| Per day | Workload | At task admission and on usage events |
| Per team | Team (aggregate of all workloads) | At task admission and on usage events |

## Mechanism

1. On task submission (or trigger-originated start), check remaining per-day and per-team budgets; reject or queue if exhausted.
2. On each usage event (tokens/tools), decrement the run budget.
3. When a run exceeds its budget, transition to `escalate`/`failed` per policy.
4. Emit budget-burn metrics for fleet review.

## Cost Tracking

Cost is derived from token usage and tool-call metadata reported by adapters, using a per-model cost table (or an external router like TierForge).

### Usage Event Schema

Each usage event from an adapter includes:

| Field | Contents |
|-------|----------|
| `run_id` | Run identifier |
| `workload` | Workload name |
| `team` | Owning team |
| `model_identity` | Provider, family, version |
| `input_tokens` | Prompt tokens |
| `output_tokens` | Completion tokens |
| `tool_calls` | Tool call count and IDs |
| `cost_usd` | Computed cost for this event |
| `timestamp` | Event time |

## Cost Showback

Cost showback attributes spend to teams and agents for fleet-level visibility (PRD 05: cost & ROI, CUJ-9):

### Attribution Model

| Dimension | Source | Purpose |
|-----------|--------|---------|
| Team | `metadata.team` from manifest | Team-level spend rollup |
| Agent (workload) | `metadata.name` | Per-agent spend |
| Run | `run_id` | Per-run spend |
| Model | `model_identity` | Spend by model (identify expensive models) |
| Tool | Tool call metadata | Spend by tool (identify expensive tools) |

### Showback Records

Cost attribution records are computed continuously from usage events:

| Field | Contents |
|-------|----------|
| `period` | Day / week / month |
| `team` | Team name |
| `workload` | Workload name |
| `total_spend_usd` | Total spend in period |
| `completed_tasks` | Count of `completed` runs |
| `failed_tasks` | Count of `failed` runs |
| `escalated_tasks` | Count of escalated runs |
| `cost_per_completed_task_usd` | `total_spend_usd / completed_tasks` |
| `waste_usd` | Spend on failed/escalated/cancelled runs |
| `roi_flag` | `high_value`, `balanced`, `expensive_low_value` |

## Cost-Per-Completed-Task

Cost-per-completed-task is the real cost of getting a task done — not just the cost of the successful run (PRD 05: cost & ROI):

```
cost_per_completed_task = (total_spend_for_workload_in_period) / (completed_tasks_in_period)
```

This includes:
- **Retries** — if a task is retried 3 times before succeeding, all 3 runs' costs are included.
- **Failed loops** — runs that failed before the successful run.
- **Escalations** — runs that were escalated (human review time is not included, but the run spend is).
- **Tool-call overhead** — tool calls that didn't contribute to completion but consumed budget.

This metric is more meaningful than per-call cost because it reflects the true cost of agent productivity.

## Waste Detection

Waste is spend that did not produce a completed task (PRD 05: cost & ROI):

| Waste Category | Definition |
|----------------|------------|
| `failed_run` | Spend on runs that ended in `failed` |
| `cancelled_run` | Spend on runs that were cancelled by operators |
| `escalated_unresolved` | Spend on runs that escalated and were never resolved |
| `retry_overhead` | Excess spend from retries beyond the first successful run |
| `idle_spend` | Spend on scheduled/watch runs that produced no actionable output |

Waste is surfaced in the fleet review and spend view. High-waste agents are flagged for budget tightening or retirement.

## ROI Flags

ROI flags classify agents by spend vs. observed value (PRD 05: cost & ROI):

| Flag | Condition | Action |
|------|-----------|--------|
| `high_value` | Low waste, high completion rate, reasonable cost-per-task | Keep |
| `balanced` | Moderate spend, acceptable completion rate | Monitor |
| `expensive_low_value` | High spend, low completion rate, or high waste | Review for retirement or re-certification |

ROI flags are computed per period and surfaced in the weekly fleet review (CUJ-9) and the [operator UI](operator-ui-design.md) spend view.

### Value Signals

"Observed value" is derived from:
- Completion rate (successful runs / total runs)
- Whether runs produced actionable output (for watch/scheduled modes)
- Escalation rate (high escalation = low autonomous value)
- Whether fan-out results were acknowledged/acted upon by teams

## Budget Enforcement with Cost Showback Integration

Budget enforcement and cost showback share the same usage-event stream:

1. Adapter reports a usage event.
2. Budget service decrements run/day/team budgets (enforcement).
3. Cost attribution service records the spend for showback (attribution).
4. If budget is exhausted, policy engine transitions the run to `escalate`/`failed`.
5. On terminal state, the run's total cost is finalized and attributed to the team/workload.

## Open Questions

- handling of streaming usage and partial cost
- grace margin before hard stop
- per-team aggregate limits vs per-workload limits
- whether cost-per-completed-task should exclude watch/scheduled runs (they have different value profiles)
- how to attribute shared costs (e.g., a tool call that benefits multiple workloads)
- ROI flag threshold configurability per team

## See Also

- [Workload manifest](workload-manifest-design.md) — budget fields, per-run/per-day/per-team ceilings
- [Run lifecycle](run-lifecycle-design.md) — budget checks at admission and during execution
- [Policy engine](policy-engine-design.md) — budget state as policy input, escalation on budget exhaustion
- [State store](state-store-design.md) — usage events, cost attributions
- [Telemetry](telemetry-design.md) — budget burn metrics, cost showback metrics
- [Operator UI](operator-ui-design.md) — spend view, ROI flags
- [Design decisions](design-decisions.md) — DD-04
