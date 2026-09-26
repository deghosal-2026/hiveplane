# D35: Cost, Showback & ROI v2 Design

> Status: draft

**Milestones:** M49–M50 · **Extends:** D14

## Problem

D14 attributes cost to teams and agents, but v0.2.0 makes cost an isolation, billing, and control surface: every usage event must resolve to a tenant, team, agent, run, and pipeline; tenants and teams become first-class; budgets become period-scoped with carry; spend must be estimated, forecast, capped, and billable; and model-tier routing plus an attested result cache must make spend both cheaper and legible. An un-attributed dollar is a security and finance defect, not a display gap.

## Overview

```
 usage event (model/tool/cache)         ┌─────────────────────────────┐
 ┌──────────────┐  tenant/team/agent/   │ Cost Service v2             │
 │ Adapter seam │  run/pipeline tags    │  attribution → periods      │
 └──────┬───────┘──────────────────────▶│  estimates · tier routing   │
        │                               │  caps · cache · metering    │
 ┌──────┴───────┐   admission check     └───────┬─────────┬───────────┘
 │ Budget/Policy│◀──── estimate, cap ───────────┘         │
 │ (D5/D30)     │                                          ▼
 └──────────────┘                        showback · ROI dashboards
 ┌──────────────┐  re-cert invalidation  · chargeback API
 │ Cert (D26)   │───────────────────────▶ cache eviction
 └──────────────┘
```

## Design

### Attribution (zero un-attributed usage)

Every usage event is tagged at emission: `tenant_id`, `team_id`, `workload_id`, `run_id`, `pipeline_run_id` (nullable), `step_id` (nullable), `model`, `cost_type`. The store rejects — never silently nulls — an event missing `tenant_id`/`workload_id`; an `unattributed` counter is exported and must be zero in the field test. Attribution precedence is explicit run tags → workload manifest owner → pipeline owner; unresolved events are dead-lettered for reconciliation, never billed to a default tenant. See `metering_events` in D21.

### Teams and tenants as first-class entities

`tenants` is the isolation boundary; `teams` are attribution and policy scope within a tenant. Every workload, run, budget, and metering row carries both. Attribution keys (`tenant/team/workload`) are stable and human-readable so showback, metering, and chargeback resolve the same owner.

### Budget periods and carry

Budgets are buckets — `day`, `week` (ISO), `month` (calendar) — per tenant/team/agent. A `carry` rule controls rollover: `none` (reset), `capped` (roll over up to a cap), `full` (roll over all). Rollover is idempotent, materialized by `(scope, period)`; late events are reconciled into the correct bucket by `occurred_at`. Per-period caps enforce at admission and on every usage event.

### Threshold alerts

Each period has thresholds (default `[50, 80, 100]`) evaluated on burn. Crossing fires a `cost.budget_alert` (D15 fan-out; Slack by default) exactly once per threshold per period via a `(period, threshold)` dedup key, carrying burn, remaining, and projected end-of-period spend.

### Cost-per-completed-task v2

`CPCT = total_cost(scope, period) / completed_tasks(scope, period)`. The numerator includes retries, failed loops, and escalations (agent spend plus estimated human cost). A cache hit that completes a task is credited at the cached result's original cost basis, not zero, so CPCT does not overstate savings; `cache_savings_usd` is reported separately.

### Pre-admission cost estimates

At admission, estimate expected cost by task type from history (per workload, p50/p90 of comparable tasks keyed by a task-type signature). Return `estimate_usd`, a tolerance band, and `confidence` (low until ≥N samples). A workload may require approval when the p90 estimate exceeds its remaining period budget; estimates are advisory unless policy says otherwise.

### Model-tier routing

Manifests declare `model_tier` per environment: `staging` → cheap/local tier, `production` → strong tier, with per-workload override. Routing selects the concrete model at the provider seam (D17) before the call; the chosen model is recorded in the usage event and the attestation binding. Runtime model-identity mismatch is still caught by D17/D26.

### Per-tenant spend caps (fail closed)

Tenant caps are hard: when period spend — or the p90 estimate of a pending run — would exceed the cap, admission is denied with `tenant_spend_cap_exceeded`. Caps fail closed: if cap state cannot be read, admission is denied rather than allowed. Suspended tenants (D38) are denied first.

### Result cache with attestation

Cache key = hash(task input + workload id + manifest version + agent bundle hash + effective config + model tier). A hit requires a valid attestation for that key's agent version. Entries are invalidated on re-certification (D26), provenance change, policy-pack version change, or TTL expiry. Hits emit a `metering_event` with `cost_type=cache_hit`, `cost_usd=0`, and `saved_usd` equal to the counterfactual cost; hit rate and savings appear in showback. Caching is off for side-effecting tasks unless explicitly marked deterministic.

### Chargeback metering API

Distinct from showback: an immutable per-tenant usage ledger keyed by `(tenant, period, attribution_key)` for billing. Exports are signed and reproducible; adjustments are new compensating rows, never edits.

### Budget analytics

Burn forecasts extrapolate the period from observed burn; overrun prediction returns `projected_overrun_usd` and the probability of exceeding the cap before period end. Forecasts feed alerts, admission estimates, and the ROI dashboard.

### Fleet ROI dashboards

Spend vs outcome by tenant/team/agent, trend lines, `expensive_low_value` flags (high spend, low completion/ROI), and top/bottom ROI agents. Flags reuse D14 waste signals plus CPCT-vs-peer and value signals; every flag carries evidence links.

## Data Model

| Table | Key columns |
|-------|-------------|
| `metering_events` | `id`, `tenant_id`, `team_id`, `workload_id`, `run_id`, `pipeline_run_id`, `cost_type`, `model`, `cost_usd`, `saved_usd`, `occurred_at` |
| `cost_periods` | `tenant_id`, `team_id`, `workload_id`, `period`, `total_cost_usd`, `cost_per_completed_task`, `cache_savings_usd`, `roi_flag`, `carry_in_usd` |
| `budget_periods` | `scope`, `scope_id`, `period`, `limit_usd`, `carry_rule`, `spent_usd` |
| `budget_alerts` | `period`, `threshold`, `fired_at` (unique `(period, threshold)`) |
| `tenant_spend_caps` | `tenant_id`, `period`, `cap_usd`, `enforced` |
| `cost_estimates` | `workload_id`, `task_type`, `p50_usd`, `p90_usd`, `samples` |
| `result_cache` | `cache_key` PK, `attestation_id`, `result_ref`, `saved_usd`, `created_at`, `expires_at` |

## Interfaces/API

```
GET  /cost/showback/{tenant}/{team}?period=...
GET  /cost/roi/fleet?period=...
GET  /cost/estimates/{workload_id}?task_type=...
GET  /cost/metering/usage?tenant=...&period=...&group_by=team|agent
POST /cost/metering/export   { tenant, period }        → signed usage ledger
POST /cost/cache/lookup      { task_input, workload_id, manifest_version, config }
POST /cost/cache/store       { cache_key, result_ref, attestation_id }
```

`hiveplane cost` renders showback; `hiveplane cost forecast` shows overrun prediction.

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Missing attribution tag | Event dead-lettered; `unattributed` counter increments; never billed to a default tenant |
| Cap state unreadable | Admission denied (fail closed) with `tenant_spend_cap_exceeded` |
| Cache entry from stale cert | Lookup miss; entry evicted on re-cert event |
| Period rollover race | Idempotent materialization keyed by `(scope, period)`; late events reconciled by `occurred_at` |
| Model missing from cost table | Costed at an `unknown_model` rate and flagged; never silently zero |

## Security

Metering rows are tenant-scoped and append-only; chargeback exports are signed. Estimates and cache keys are tenant-scoped and must not leak cross-tenant task content; cached results are encrypted at rest and access-checked by tenant. Caps and admission denials are audited (DD-07). Attestation binding prevents serving a result a re-certification invalidated.

## Testing

- Attribution completeness: fuzz usage events; assert zero un-attributed after reconciliation.
- Period rollover and carry across day/week/month boundaries, including late events.
- A threshold crossing fires exactly once per `(period, threshold)`.
- CPCT includes retries/failed/escalations; cache-hit accounting does not overstate savings.
- Estimates within tolerance on seeded history; tier routing picks the configured model per environment.
- Cap hard-stops admission; cache hit reuses and re-cert invalidates; chargeback export verifies.

## Open Questions

- Should `capped` carry be a percentage or an absolute amount?
- Is the human-review rate per tenant, or global with per-team override?
- Do pipeline budgets roll up to team/tenant, or stay pipeline-scoped?
- Should cache savings credit the tenant, or be split with the platform?

## See Also

- [PRD 05: Features](../prd/05-features.md) — Cost & ROI theme
- [PRD 09: Roadmap](../prd/09-roadmap.md) — pillar J
- [WBS v0.2.0 Part 13](../wbs/v0.2.0/wbs-v0.2.0-part13-cost-roi.md) — M49–M50
- [Cost Service Design](cost-service-design.md) (D14) — the v0.1.0 attribution/CPCT baseline
- [Budget Enforcement Design](budget-enforcement-design.md) (D5)
- [Runtime Guards Design](runtime-guards-design.md) (D30)
- [Certification v2 Design](certification-v2-design.md) (D26)
- [Fleet Control Data Model Design](fleet-control-data-model-design.md) (D21)
- [Reporting, Tenancy & Distribution Design](reporting-tenancy-distribution-design.md) (D38)
- [Operator Experience Design](operator-experience-design.md) (D36)
- [Design Decisions](design-decisions.md) — DD-04
