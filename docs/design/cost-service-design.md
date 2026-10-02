# D14: Cost Service Design

> Status: draft
> **v0.2.0:** extended by [Cost, Showback & ROI v2 (D35)](cost-roi-v2-design.md).

## Problem

Agent spend is reported after the fact in aggregate dashboards that tell you what was spent but not whether it was worth it. There is no cost-per-completed-task, no waste detection, no ROI signal, and no enforcement linkage. Teams cannot answer: which agents are expensive but low-value, which are burning budget on retries and failed loops, and which policies generate the most escalations (each escalation is human cost).

HivePlane attributes cost to teams and agents in real time, computes cost-per-completed-task (not per-call), detects waste, flags ROI, and integrates with budget enforcement so spend is controlled before it happens, not mourned after.

## Overview

```
 Usage Events                    Cost Service                     Output
 ┌───────────┐                  ┌──────────────────────┐          ┌──────────────┐
 │ Adapter   │  token usage     │  ┌────────────────┐  │          │ Showback     │
 │ reports   │─────────────────▶│  │ Cost Attribution│  │          │ Views        │
 │ tool calls│  tool cost       │  │ (team + agent)  │──┼─────────▶│ (UI, API)    │
 └───────────┘                  │  └───────┬────────┘  │          └──────────────┘
 ┌───────────┐                  │          │           │          ┌──────────────┐
 │ Budget    │  enforcement     │  ┌───────▼────────┐  │          │ ROI Flags    │
 │ Enforcer  │◀─────────────────│  │ Cost-per-      │──┼─────────▶│ (weekly      │
 │ (D5)      │  current burn    │  │ Completed-Task │  │          │  fleet       │
 └───────────┘                  │  └───────┬────────┘  │          │  review)     │
 ┌───────────┐                  │          │           │          └──────────────┘
 │ Run state │  completed/      │  ┌───────▼────────┐  │          ┌──────────────┐
 │ (D2)      │  failed/         │  │ Waste Detection│──┼─────────▶│ Budget       │
 └───────────┘  escalated       │  │ + ROI Flags    │  │          │ Enforcement  │
                                │  └────────────────┘  │          │ (D5)         │
                                └──────────────────────┘          └──────────────┘
```

## Cost Attribution

### Cost Sources

| Source | How cost is derived |
|--------|---------------------|
| Model token usage | Tokens × per-model price (from a cost table or external router like TierForge) |
| Tool calls | Per-tool cost (if the tool has a metered cost, e.g., an API call with a price) |
| Sandbox compute | CPU-seconds, memory-MB-seconds, wall-clock (if running on metered infra) |
| Human review time | Escalations → estimated human minutes × blended hourly rate (configurable) |

### Attribution Model

Every usage event is tagged with:

```json
{
  "run_id": "run-abc123",
  "workload_id": "incident-triage-agent",
  "team": "platform",
  "manifest_version": 5,
  "cost_type": "model_tokens",
  "model": "gpt-4o-2024-08-06",
  "input_tokens": 1200,
  "output_tokens": 800,
  "cost_usd": 0.012,
  "timestamp": "2026-09-12T10:01:23Z"
}
```

Cost is attributed at three levels:

1. **Per run** — the total cost of a single run, including all retries, tool calls, and model calls.
2. **Per workload** — aggregated across all runs of a workload in a time period.
3. **Per team** — aggregated across all workloads owned by a team.

### Cost Table

The cost service maintains a per-model cost table:

```yaml
model_costs:
  gpt-4o-2024-08-06:
    input_per_1m_tokens: 2.50
    output_per_1m_tokens: 10.00
  claude-3-5-sonnet-20241022:
    input_per_1m_tokens: 3.00
    output_per_1m_tokens: 15.00
  llama-3.1-70b-local:
    input_per_1m_tokens: 0.0
    output_per_1m_tokens: 0.0
    note: "self-hosted; cost = compute only"
```

The cost table is versioned and editable by operators. Changes are audited.

## Cost-per-Completed-Task

### Definition

Cost-per-completed-task (CPCT) is the real cost of producing one successful outcome, including all the work that didn't succeed:

```
CPCT = total_cost(workload, period) / completed_tasks(workload, period)
```

Where:
- `total_cost` includes: model tokens, tool calls, sandbox compute, and human review time for all runs (successful, failed, retried, escalated) in the period.
- `completed_tasks` is the count of runs that reached `completed` state with a successful outcome.

A run that failed and was retried contributes its cost to the numerator but not the denominator. A run that was escalated and resolved by a human contributes both the agent cost and the human cost to the numerator.

### Why Not Per-Call

Per-call cost is misleading. An agent that makes 100 cheap calls but never completes a task is more expensive than one that makes 10 expensive calls and completes every time. CPCT reflects the real economics of the agent.

### CPCT Components

```json
{
  "workload_id": "incident-triage-agent",
  "period": "2026-09-01T00:00:00Z/2026-09-12T00:00:00Z",
  "cpct_usd": 0.42,
  "total_cost_usd": 126.00,
  "completed_tasks": 300,
  "total_runs": 380,
  "cost_breakdown": {
    "model_tokens": 89.00,
    "tool_calls": 15.00,
    "sandbox_compute": 4.00,
    "human_review": 18.00
  },
  "failed_runs_cost": 21.00,
  "escalated_runs_cost": 18.00,
  "retry_cost_ratio": 0.167
}
```

## Waste Detection

### Definition

Waste is spend on agents that produce low or no value. The cost service detects waste using multiple signals:

| Signal | Threshold | Description |
|--------|-----------|-------------|
| High CPCT vs. peers | CPCT > 2× team median | Agent costs significantly more per completed task than comparable agents |
| Low completion rate | < 30% of runs reach `completed` | Agent fails most of the time; cost is wasted on failed attempts |
| High escalation rate | > 40% of runs are escalated | Agent cannot resolve without human intervention; agent cost + human cost |
| High retry cost ratio | retry_cost > 25% of total | Significant cost is in retries and failed loops |
| Zero completed tasks (period) | 0 completions in 7 days | Agent is running but producing nothing |
| High spend, low activity | spend > $100/week, < 5 runs | Agent is expensive but rarely used (potential misconfiguration or abandoned) |

### Waste Detection Output

```json
{
  "workload_id": "repo-analysis-agent",
  "team": "dev-tools",
  "flags": [
    {
      "type": "high_cpct_vs_peers",
      "value": 1.85,
      "threshold": 2.0,
      "team_median_cpct": 0.22,
      "workload_cpct": 0.41,
      "severity": "warning"
    },
    {
      "type": "low_completion_rate",
      "value": 0.25,
      "threshold": 0.30,
      "severity": "critical"
    }
  ],
  "recommendation": "Agent has low completion rate (25%) and above-median CPCT. Review prompt/model or consider retirement.",
  "estimated_waste_usd_weekly": 14.50
}
```

## ROI Flags

### Definition

ROI flags compare spend against observed outcome/value. The cost service does not define "value" — it consumes value signals from other systems and computes the ratio.

### Value Signals

| Signal | Source | Description |
|--------|--------|-------------|
| Tasks completed | Run lifecycle (D2) | Count of successful runs |
| Incidents auto-resolved | External (incident system) | Runs that resolved an incident without human action |
| MTTR reduction | External (incident metrics) | Time saved vs. baseline MTTR |
| PRs reviewed | External (GitHub) | PRs where the agent provided useful review |
| Manual work saved | Estimated | Hours of human work displaced by agent output |

### ROI Computation

```
ROI = observed_value_usd / total_cost_usd
```

Where `observed_value_usd` is derived from value signals × configurable value-per-unit:

```yaml
value_table:
  incident_auto_resolved: 500.00      # $500 per auto-resolved incident
  mttr_reduction_hour: 200.00         # $200 per hour of MTTR reduction
  pr_reviewed: 15.00                  # $15 per useful PR review
  manual_hour_saved: 75.00            # $75 per hour of manual work saved
```

### ROI Flag Thresholds

| ROI | Flag | Action |
|-----|------|--------|
| > 3.0 | `high_roi` | Surface in weekly review as a success case |
| 1.0 – 3.0 | `positive_roi` | No action |
| 0.5 – 1.0 | `low_roi` | Warning: agent is not clearly paying for itself |
| < 0.5 | `negative_roi` | Critical: agent is spending more than it delivers; consider retirement or rework |

## Showback Views

### By Team

```
Team: platform                          Period: Sep 1 – Sep 12, 2026
══════════════════════════════════════════════════════════════════

  Workloads: 4    Total Spend: $342.00    CPCT (median): $0.38

  ┌─────────────────────┬──────────┬────────┬────────┬───────┬──────┐
  │ Workload            │ Spend    │ Runs   │ Compl. │ CPCT  │ ROI  │
  ├─────────────────────┼──────────┼────────┼────────┼───────┼──────┤
  │ incident-triage     │ $126.00  │ 380    │ 300    │ $0.42 │ 2.8  │
  │ deployment-verify   │ $89.00   │ 120    │ 110    │ $0.81 │ 1.5  │
  │ repo-analysis       │ $84.00   │ 280    │ 70     │ $1.20 │ 0.4  │
  │ compliance-scan     │ $43.00   │ 12     │ 12     │ $3.58 │ 1.1  │
  └─────────────────────┴──────────┴────────┴────────┴───────┴──────┘

  Flags: repo-analysis — negative_roi, low_completion_rate
         compliance-scan — high_cpct_vs_peers (warning)
```

### By Agent (Workload Detail)

```json
{
  "workload_id": "incident-triage-agent",
  "team": "platform",
  "period": "2026-09-01/2026-09-12",
  "total_cost_usd": 126.00,
  "cpct_usd": 0.42,
  "roi": 2.8,
  "cost_breakdown": {
    "model_tokens": 89.00,
    "tool_calls": 15.00,
    "sandbox_compute": 4.00,
    "human_review": 18.00
  },
  "runs": 380,
  "completed": 300,
  "failed": 50,
  "escalated": 30,
  "retry_cost_usd": 21.00,
  "flags": []
}
```

### By Time Period

Aggregated cost over time, with trend lines:

```
Daily Spend: incident-triage-agent
$20 ┤                            ◄── spike (incident storm)
$15 ┤                  ╭──╮
$10 ┤            ╭──╮─╯  ╰──
$ 5 ┤──╮──╮──╮──╯
$ 0 ┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──
    1  2  3  4  5  6  7  8  9  10 11 12
```

## Integration with Budget Enforcement

The cost service integrates with the Budget Enforcement service (D5) in two directions:

### Cost → Budget (real-time enforcement)

Every usage event updates the current run and per-day budget burn. The budget enforcer (D5) uses this to:
- Reject new task submissions if per-day budget is exhausted.
- Fail a run if it exceeds its per-run budget.
- Emit budget-burn metrics.

### Budget → Cost (planning)

The cost service consumes budget configuration to compute:
- **Budget utilization** — what % of the period's budget has been spent.
- **Projected spend** — if current burn rate continues, will the period end over budget?
- **Budget alerts** — notify when projected spend exceeds 80% / 100% of period budget.

## Cost Service API

```
GET    /cost/showback/team/{team}
  Query: ?period_start=...&period_end=...
  → 200: { workloads: [...], team_total, team_cpct_median }

GET    /cost/showback/workload/{workload_id}
  Query: ?period_start=...&period_end=...
  → 200: { cost_breakdown, cpct, roi, runs, flags }

GET    /cost/showback/fleet
  Query: ?period_start=...&period_end=...&team=...
  → 200: { teams: [...], fleet_total, fleet_cpct_median }

GET    /cost/waste
  Query: ?team=...&severity=...
  → 200: { flags: [...] }

GET    /cost/roi
  Query: ?team=...&period_start=...&period_end=...
  → 200: { items: [...], fleet_roi_median }

POST   /cost/model-prices
  Body: { model, input_per_1m, output_per_1m }
  → 200: { updated: true }

POST   /cost/value-table
  Body: { signal, value_usd }
  → 200: { updated: true }
```

## Data Model

```
cost_events
  id              TEXT PK
  run_id          TEXT FK
  workload_id     TEXT FK
  team            TEXT
  manifest_version INTEGER
  cost_type       TEXT  -- model_tokens | tool_call | sandbox_compute | human_review
  model           TEXT  -- nullable
  cost_usd        NUMERIC(10,4)
  details         JSONB
  timestamp       TIMESTAMPTZ

cost_summaries
  workload_id     TEXT FK
  team            TEXT
  period_start    TIMESTAMPTZ
  period_end      TIMESTAMPTZ
  total_cost_usd  NUMERIC(10,2)
  cpct_usd        NUMERIC(10,2)
  roi             REAL
  completed_tasks INTEGER
  total_runs      INTEGER
  cost_breakdown  JSONB
  flags           JSONB
  PRIMARY KEY (workload_id, period_start, period_end)

model_cost_table
  model           TEXT PK
  input_per_1m    NUMERIC(10,4)
  output_per_1m   NUMERIC(10,4)
  updated_at      TIMESTAMPTZ
  updated_by      TEXT

value_table
  signal          TEXT PK
  value_usd       NUMERIC(10,2)
  updated_at      TIMESTAMPTZ
```

## Open Questions

- **Value table ownership:** who sets the value-per-unit ($500 per auto-resolved incident)? Is this per-team or global? How often is it reviewed?
- **Human review cost estimation:** how do we estimate human minutes spent on an escalation? Self-reported? Average per escalation type?
- **Cost attribution for shared resources:** if multiple workloads share a sandbox pool or a model endpoint, how is the shared cost split?
- **Real-time vs. batch:** should CPCT and waste detection be computed in real time (streaming) or batched (nightly)? Real-time is more useful but more expensive.
- **Cost anomalies:** should the cost service detect sudden spend anomalies (e.g., a workload that suddenly costs 10× more) and alert?

## See Also

- [PRD 02: Architecture](../prd/02-architecture.md) — budget & cost service in the system architecture
- [PRD 05: Features](../prd/05-features.md) — cost & ROI feature breakdown
- [Budget Enforcement Design](budget-enforcement-design.md) (D5) — per-run and per-day budget enforcement
- [Run Lifecycle Design](run-lifecycle-design.md) (D2) — run states drive cost attribution
- [Telemetry Design](telemetry-design.md) (D8) — usage events flow through telemetry pipeline
- [Operator UI Design](operator-ui-design.md) (D9) — spend/showback views in the UI
- [Agent Health Design](agent-health-design.md) (D16) — cost flags contribute to health signals
- [Design Decisions](design-decisions.md) — DD-04
