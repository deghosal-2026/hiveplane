# D31: Agent Health & SLO v2 Design

> Status: implemented for M42 (health model with MTTR + quality, SLO/error-budget
> accounting, fast/slow burn alerting, auto-quarantine/throttle on burn-through, and
> health API/CLI). M43 adds synthetic probes, approval analytics, plane `/metrics`,
> and Grafana dashboards.

**Milestones:** M42–M43 · **Extends:** D16

## Problem

D16 introduced a composite health score from readiness, failure rate, drift, escalation rate, and cost waste. It is a useful summary but not an operational contract: no MTTR, no error budget, no burn-rate alerting, no live quality signal between re-certifications, no proactive probe to catch decay before drift trips. Operators cannot see whether approvals — not agents — are the bottleneck, and the plane cannot observe itself.

D31 turns health into an SLO-backed control signal: per-workload availability and quality objectives, error-budget accounting from real events, burn-rate monitoring with debounced alerts, automatic throttle/quarantine on burn-through, synthetic probes for early drift warning, and plane self-monitoring. D31 is authoritative for v0.2.0; D16's score remains the composite summary.

## Overview

```
 run lifecycle ─┐
 drift (D26) ───┼──▶ Health Service ──▶ SLO / Error Budget (burn windows)
 online eval ───┤    (per workload)              │ burn-through
 probes (M43) ──┤                                ▼
 approvals ─────┘                 shared immune machinery: throttle|quarantine
 plane self-metrics ─▶ /metrics ─▶ Prometheus ─▶ Grafana JSONs
```

## Design

### Health Model

Per workload, over a rolling window (default 24h), retaining D16's composite:

| Signal | Source | Notes |
|--------|--------|-------|
| Readiness | run lifecycle (D2) | certified, not paused/quarantined, budget available, tools healthy |
| Recent failure rate | run lifecycle | terminal runs only |
| **MTTR** | failure/recovery events | mean time from failure to next success |
| Drift status | certification (D26) | `clean` / `detected` / `overdue` |
| **Quality score** | online eval (D27) | judge score on sampled production runs |
| SLO status / error budget | this doc | per objective |
| Breaker state | guards (D30) | tool/workload breakers open |

`insufficient_data` is returned when fewer than `min_runs_for_score` terminal runs exist in the window.

### SLO Hooks

SLOs are declared in the workload manifest and become first-class health inputs:

```yaml
spec:
  slo:
    availability: { target: 0.99, window: 30d, metric: completed_runs / terminal_runs }
    quality:      { target: 0.85, window: 7d,  metric: mean(online_eval_score) }
```

Each objective computes target, observed value, window, **error budget** = `(1 - target) * window_events`, consumed, and remaining. Consumption comes from real events — failed runs for availability, judge scores below target for quality.

### Burn-Rate Monitoring

| Window | Purpose | Alert |
|--------|---------|-------|
| Fast (1h) | sudden outage | page when fast burn ≥ 14.4× (budget exhausts in ~2 days) |
| Slow (6h) | creeping degradation | ticket when slow burn ≥ 6× |

Burn rate = `observed_error_rate / allowed_error_rate`. Alerts are **debounced**: a threshold must hold for `debounce_for` (default 10m) before firing, and resolution requires a clear window. Alert state and last-fired time are stored to prevent storms.

### Burn-Through Action

When an objective exhausts its error budget (or fast burn crosses the critical threshold for the debounce window), health triggers an automatic action through the **shared immune machinery** (D26), the same path drift quarantine uses:

| Condition | Action |
|-----------|--------|
| Fast burn critical | auto-throttle: reduce concurrency, stop new triggers |
| Error budget exhausted | quarantine workload; block new production runs |
| Sustained degraded | notify owner via fan-out (D15) |

Every action carries a reason, the objective, the burn rate, and a rule id, and is audited. Reinstatement is explicit after re-certification or budget reset.

### Synthetic Probes

Scheduled ping-tasks per workload exercise a known-good path and measure live quality/readiness.

- Each probe has a **known expected behavior** (fixture input → expected outcome) and records pass/fail, latency, and cost.
- Probes run on a **separate budget** and are tagged `probe`, so they never count toward production SLOs or cost-per-completed-task.
- Probes **cannot deliver to fan-out** and cannot create real side effects; a probe that tries is misconfigured and blocked.
- A probe failure is an **early drift warning**, raising health degradation before the scheduled drift detector (D26) trips.
- Probes are rate-limited and cheap by construction.

### Quality Scores, Approval Analytics & Self-Monitoring

Online eval (D27) judge results on sampled production runs feed health directly as the `quality_score` signal and as the quality SLO's observed value; a dip alerts before scheduled re-certification would catch it. Approval analytics are read-only and derived from approval events: **latency by approver**, **bottleneck detection** (highest wait / fewest active approvers), and **trends** (latency and approve/reject rates). The plane exports its own Prometheus metrics at `/metrics` (queue depth, run throughput, reconciliation lag, store latency, guard trips, API errors) and ships Grafana dashboard JSONs under `deploy/grafana/` for fleet, health, cost, and plane health. Self-monitoring is **decoupled** from what it observes: the endpoint is served by the process with no dependency on the health service or its database, so a failing health service cannot blind the plane.

## Data Model

```
workload_slo
  workload_id, objective,        -- availability | quality
  target, window, error_budget, consumed, status, updated_at
  PRIMARY KEY (workload_id, objective)

burn_rate_samples
  id, workload_id, objective, window, burn_rate, observed_at

probe_runs
  id, workload_id, passed, latency_ms, cost_usd, detail JSONB, created_at
```

## Interfaces / API

```
GET /health/workloads/{id}          # model + SLO + burn + budget
GET /health/workloads/{id}/slo
GET /health/workloads/{id}/burn
GET /health/probes?workload_id=...
GET /analytics/approvals
GET /metrics                        # plane self-metrics (Prometheus)
```

CLI: `hiveplane health`, `hiveplane health show <workload>`, `hiveplane probes list`.

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Online eval unavailable | quality signal marked stale; SLO uses last-good with a timestamp |
| Probe fails, production healthy | early warning raised; no quarantine until burn/drift confirms |
| Alert storm from one objective | debounce + last-fired suppression prevents repeated pages |
| Metrics endpoint overloaded | scrape work is bounded; health-service failure does not affect it |

## Security

- Approval analytics expose operator identities only to authorized roles (D33).
- Probe inputs are fixtures, never live secrets; probe results carry no secret material.
- Burn-through actions are audited with actor `system` and the triggering objective (DD-07).

## Testing

- Readiness flips correctly on certify/pause/quarantine/tool-health changes.
- Error-budget consumption is computed from real failures and quality dips.
- Burn-rate fast/slow windows alert at thresholds and debounce.
- Burn-through throttles or quarantines via the shared immune machinery.
- MTTR is computed from real failure/recovery events.
- A probe detects seeded decay before the drift threshold trips; probes cannot reach fan-out.
- `/metrics` exposes the expected series and the shipped dashboards render.

## Open Questions

- Should quality SLOs use mean judge score or proportion-above-threshold?
- Are probe schedules global or per-team, and who owns probe cost?
- Should burn-through ever auto-reinstate after a cooldown, or always require a human?

## See Also

- [Agent Health Design](agent-health-design.md) (D16) — composite score retained; D31 is authoritative
- [Runtime Guards Design](runtime-guards-design.md) (D30) — breaker and velocity signals
- [Defense & Policy v2 Design](defense-policy-v2-design.md) (D29) — quarantine reason/rule shape
- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — drift and re-certification
- [Result Fan-out Design](result-fanout-design.md) (D15) — degradation notifications
- [PRD 05: Features](../prd/05-features.md) — Observability & Health
- [WBS Part 9](../wbs/v0.2.0/wbs-v0.2.0-part9-guards-health.md) — M42; [WBS Part 10](../wbs/v0.2.0/wbs-v0.2.0-part10-probes-mcp.md) — M43
