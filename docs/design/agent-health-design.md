# D16: Agent Health Design

> Status: draft
> **v0.2.0:** extended by [Agent Health & SLO v2 (D31)](agent-health-slo-design.md).

## Problem

Teams cannot answer a basic fleet question: "is this agent healthy right now?" There is no first-class health signal. Failure rate is buried in metrics, drift is undetected, and SLO status is not computed. A fleet is only operable if health is a first-class concept that aggregates readiness, failure rate, SLO status, and drift into a single signal per agent — and rolls up to a fleet-level view.

HivePlane defines an agent health model with rolling-window health calculation, fleet aggregation, and integration with the drift detector: drift degrades health, sustained degradation triggers quarantine.

## Overview

```
 Health Signals                       Agent Health Service
 ┌──────────────┐                    ┌──────────────────────────────┐
 │ Run Lifecycle│  recent runs       │  ┌──────────────────────┐   │
 │ (D2)         │───────────────────▶│  │ Health Calculator    │   │
 └──────────────┘  failure rate      │  │ (rolling window)     │   │
 ┌──────────────┐                    │  └──────────┬───────────┘   │
 │ Cert Pipeline│  drift indicators  │             │               │
 │ (D10)        │───────────────────▶│  ┌──────────▼───────────┐   │
 └──────────────┘                    │  │ Fleet Aggregator      │   │
 ┌──────────────┐                    │  │ (team + fleet rollup)│   │
 │ Cost Service │  waste flags       │  └──────────┬───────────┘   │
 │ (D14)        │───────────────────▶│             │               │
 └──────────────┘                    │  ┌──────────▼───────────┐   │
 ┌──────────────┐                    │  │ Health Dashboard     │   │
 │ Telemetry    │  SLO status        │  │ Integration (D9)     │   │
 │ (D8)         │───────────────────▶│  └──────────────────────┘   │
 └──────────────┘                    └──────────────────────────────┘
                                              │
                                    drift → degradation → quarantine
```

## Health Signals

### Signal Inventory

| Signal | Source | Description |
|--------|--------|-------------|
| **Readiness** | Run Lifecycle (D2) | Is the agent accepting runs? (not paused, not quarantined, not blocked by budget) |
| **Recent failure rate** | Run Lifecycle (D2) | Percentage of runs that ended in `failed` state in the rolling window |
| **SLO status** | Telemetry (D8) | Is the agent meeting its availability and quality SLOs? |
| **Drift indicators** | Certification Pipeline (D10) | Has drift been detected? Is re-certification overdue? |
| **Cost waste flags** | Cost Service (D14) | Has the agent been flagged for waste or negative ROI? |
| **Escalation rate** | Policy Engine (D4) | Percentage of runs that escalated to human approval |
| **Tool health** | MCP Tool Registry (D13) | Are the agent's required tools healthy? |

### Signal Details

#### Readiness

A workload is **ready** if all of the following are true:
- Certification status is `certified` (or `provisional` for staging context)
- Not paused by an operator
- Not quarantined
- Per-day budget is not exhausted
- Required tools are not `unhealthy`

#### Recent Failure Rate

Computed over a rolling window (default: 24 hours):

```
failure_rate = failed_runs(window) / total_runs(window)
```

Only runs that reached a terminal state (`completed`, `failed`, `cancelled`) are counted. Paused and running states are excluded.

#### SLO Status

Each workload can define SLOs:

```yaml
slo:
  availability:
    target: 0.95
    window: 7d
    metric: completed_runs / total_runs
  quality:
    target: 0.80
    window: 7d
    metric: successful_outcomes / completed_runs
  latency_p95:
    target_ms: 30000
    window: 24h
    metric: p95(run_duration_ms)
```

SLO status is computed by comparing the actual metric against the target:

| Status | Condition |
|--------|-----------|
| `meeting` | metric ≥ target |
| `at_risk` | metric within 5% of target (breach imminent) |
| `breaching` | metric < target |

#### Drift Indicators

| Indicator | Condition |
|-----------|-----------|
| `drift_detected` | Drift detector (D10) has flagged drift in the most recent re-certification |
| `re_cert_overdue` | Scheduled re-certification has not run within the expected interval + grace period |
| `cert_expiring` | Certification is older than the max cert age (default: 30 days) |

## Health Calculation

### Per-Agent Health Score

The health service computes a composite health score for each workload:

```
function compute_health(workload, window=24h):

  signals = collect_signals(workload, window)

  # Hard blockers — any of these immediately set health to critical
  if signals.certification_status == "quarantined":
    return { status: "critical", reason: "quarantined", score: 0 }

  if signals.certification_status == "uncertified":
    return { status: "critical", reason: "uncertified", score: 0 }

  if not signals.ready:
    return { status: "degraded", reason: "not_ready: " + signals.not_ready_reason, score: 30 }

  # Weighted scoring
  score = 100

  # Failure rate (weight: 30)
  if signals.failure_rate > 0.20:
    score -= 30
  elif signals.failure_rate > 0.10:
    score -= 20
  elif signals.failure_rate > 0.05:
    score -= 10

  # SLO status (weight: 25)
  for slo in signals.slo_status:
    if slo.status == "breaching":
      score -= 15
    elif slo.status == "at_risk":
      score -= 8

  # Drift indicators (weight: 20)
  if signals.drift_detected:
    score -= 20
  if signals.re_cert_overdue:
    score -= 10

  # Escalation rate (weight: 15)
  if signals.escalation_rate > 0.40:
    score -= 15
  elif signals.escalation_rate > 0.20:
    score -= 8

  # Cost waste (weight: 10)
  if signals.waste_flag_severity == "critical":
    score -= 10
  elif signals.waste_flag_severity == "warning":
    score -= 5

  # Clamp
  score = max(0, min(100, score))

  if score >= 80:
    return { status: "healthy", score, signals }
  elif score >= 50:
    return { status: "degraded", score, signals }
  else:
    return { status: "critical", score, signals }
```

### Health Statuses

| Status | Score Range | Meaning | Action |
|--------|-------------|---------|--------|
| `healthy` | 80–100 | Agent is operating normally | None |
| `degraded` | 50–79 | One or more signals are concerning | Monitor; notify owner if sustained |
| `critical` | 0–49 | Agent is unhealthy or blocked | Immediate attention; auto-actions may apply |

### Rolling Window

Health is computed over a rolling window. The default window is 24 hours, but it is configurable per workload:

```yaml
health:
  window: 24h
  min_runs_for_score: 5  # if fewer than 5 runs in window, health is "insufficient_data"
  sustained_degraded_threshold: 1h  # if degraded for > 1h, notify owner
  sustained_critical_threshold: 15m  # if critical for > 15m, auto-action
```

If fewer than `min_runs_for_score` runs exist in the window, health status is `insufficient_data` rather than a computed score.

## Fleet Health Aggregation

### Team-Level Rollup

```
Team: platform
  Agents: 4
  Healthy: 2 | Degraded: 1 | Critical: 1

  ┌─────────────────────┬────────┬──────────────────────────────┐
  │ Workload            │ Health │ Key signal                   │
  ├─────────────────────┼────────┼──────────────────────────────┤
  │ incident-triage     │ ● 85   │ —                            │
  │ deployment-verify   │ ● 92   │ —                            │
  │ repo-analysis       │ ◐ 62   │ failure_rate: 0.15           │
  │ compliance-scan     │ ● 0    │ quarantined (drift)          │
  └─────────────────────┴────────┴──────────────────────────────┘
```

### Fleet-Level Rollup

```
Fleet: all teams
  Agents: 12
  Healthy: 8 | Degraded: 2 | Critical: 2
  Fleet health score: 72 (median)

  ┌────────────┬────────┬────────────────────────────────┐
  │ Team       │ Agents │ Health                         │
  ├────────────┼────────┼────────────────────────────────┤
  │ platform   │ 4      │ 2 healthy, 1 degraded, 1 crit │
  │ dev-tools  │ 3      │ 3 healthy                      │
  │ security   │ 2      │ 1 healthy, 1 degraded          │
  │ data       │ 3      │ 2 healthy, 1 critical          │
  └────────────┴────────┴────────────────────────────────┘
```

## Integration with Drift Detector

The health service consumes drift indicators from the certification pipeline (D10):

### Drift → Health Degradation

When the drift detector flags drift:
1. The health service receives a `drift_detected` signal.
2. The agent's health score drops (drift carries weight 20 in the scoring).
3. If drift causes the agent to be quarantined, health drops to `critical` (score 0).

### Sustained Degradation → Auto-Action

If an agent's health is `degraded` or `critical` for longer than the configured sustained threshold:
- `degraded` for > `sustained_degraded_threshold` (default: 1h) → notify the owning team (via fan-out, D15).
- `critical` for > `sustained_critical_threshold` (default: 15m) → auto-action:
  - If the cause is drift → trigger immediate re-certification (D10).
  - If the cause is high failure rate → pause the workload (stop accepting new triggered runs, allow in-flight runs to complete).
  - If the cause is tool unhealthiness → notify tool owner; block new runs.

### Health → Quarantine Path

```
 drift detected
      │
      ▼
 health: degraded (drift signal)
      │
      │ sustained > 15m
      ▼
 auto re-certification triggered
      │
      ├── re-cert passes → health recovers, drift signal clears
      │
      └── re-cert fails → quarantined → health: critical (score 0)
```

## Health Dashboard Integration

The health service feeds the Operator UI (D9):

### Fleet Health Dashboard

- **Fleet-level score** (median or weighted average across all agents)
- **Team breakdown** — agents per team, health distribution
- **Agent list** — sorted by health score (critical first), with key signal per agent
- **Trend view** — health score over time per agent (line chart)
- **Alert feed** — recent health state changes (degraded → critical, quarantine events)

### Per-Agent Health View

- **Current health score** with breakdown by signal
- **Rolling window** — runs, failures, escalations in the window
- **SLO status** — each SLO with target, actual, and status
- **Drift history** — recent drift detection events and re-certification results
- **Cost signals** — waste flags and ROI status
- **Health timeline** — when did the agent enter degraded/critical, what caused it

## Health Service API

```
GET    /health/workloads/{workload_id}
  → 200: { status, score, signals, window, computed_at }

GET    /health/workloads
  Query: ?team=...&status=...&min_score=...
  → 200: { items: [...], fleet_score }

GET    /health/teams/{team}
  → 200: { team, agents: [...], team_score }

GET    /health/fleet
  → 200: { fleet_score, teams: [...], agents: [...] }

GET    /health/workloads/{workload_id}/history
  Query: ?period_start=...&period_end=...
  → 200: { timeline: [...] }

POST   /health/workloads/{workload_id}/pause
  Body: { reason, operator }
  → 200: { paused: true }

POST   /health/workloads/{workload_id}/resume
  Body: { operator }
  → 200: { resumed: true }
```

## Data Model

```
agent_health
  workload_id     TEXT PK
  status          TEXT  -- healthy | degraded | critical | insufficient_data
  score           INTEGER
  signals         JSONB
  window          TEXT  -- e.g., "24h"
  computed_at     TIMESTAMPTZ
  degraded_since  TIMESTAMPTZ  -- nullable
  critical_since  TIMESTAMPTZ  -- nullable

agent_health_history
  id              TEXT PK
  workload_id     TEXT FK
  status          TEXT
  score           INTEGER
  signals         JSONB
  computed_at     TIMESTAMPTZ

fleet_health
  id              TEXT PK  -- snapshot ID
  fleet_score     INTEGER
  team_scores     JSONB
  agent_count     INTEGER
  healthy_count   INTEGER
  degraded_count  INTEGER
  critical_count  INTEGER
  computed_at     TIMESTAMPTZ
```

## Open Questions

- **Health score weights:** are the default weights (failure rate 30, SLO 25, drift 20, escalation 15, cost 10) the right balance? Should they be configurable per workload?
- **Cross-agent health:** should one agent's health affect another's? (e.g., if a shared tool is unhealthy, all agents using it degrade)
- **Health-based routing:** should the trigger service (D12) prefer healthier agents when multiple agents could handle an event?
- **Historical retention:** how long is health history retained? At what granularity (every computation, or sampled)?
- **Self-healing:** beyond auto-quarantine and auto-pause, should the health service attempt any corrective action (e.g., rolling back to a previous manifest version)?

## See Also

- [PRD 02: Architecture](../prd/02-architecture.md) — agent health in the system architecture
- [PRD 05: Features](../prd/05-features.md) — observability & health feature breakdown
- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — drift indicators feed health
- [Run Lifecycle Design](run-lifecycle-design.md) (D2) — run states drive failure rate and readiness
- [Telemetry Design](telemetry-design.md) (D8) — SLO metrics from telemetry pipeline
- [Cost Service Design](cost-service-design.md) (D14) — waste flags feed health
- [MCP Tool Registry Design](mcp-tool-registry-design.md) (D13) — tool health affects agent health
- [Result Fan-out Design](result-fanout-design.md) (D15) — health degradation notifications
- [Operator UI Design](operator-ui-design.md) (D9) — health dashboard
- [Design Decisions](design-decisions.md) — DD-12, DD-06
