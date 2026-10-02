# WBS v0.2.0 — Part 13: Cost, Showback & ROI

**Milestones:** M49–M50 · **Part:** 13 of 19

## Goal

Make cost legible and controllable. Attribute spend by tenant → team → agent, compute true cost-per-completed-task, forecast and cap spend, route by model tier, cache results, expose metering for billing, and surface fleet ROI.

## M49 — Cost Showback, Teams/Tenants & Budget Periods/Alerts

**Objective:** Attribute spend accurately across the fleet, make teams/tenants first-class, add budget periods with threshold alerts, and expose cost-per-completed-task.

**Work items:**

- [x] [#370](https://github.com/deghosal-2026/hiveplane/issues/370) — M49-01 — Cost attribution: every model/tool usage event tagged with tenant, team, agent, run, pipeline
- [x] [#371](https://github.com/deghosal-2026/hiveplane/issues/371) — M49-02 — Teams/tenants as first-class entities (org model + membership + attribution)
- [x] [#372](https://github.com/deghosal-2026/hiveplane/issues/372) — M49-03 — Budget periods: day/week/month buckets per tenant/team/agent with carry rules
- [x] [#373](https://github.com/deghosal-2026/hiveplane/issues/373) — M49-04 — Threshold alerts: notify at configurable percentages (e.g., Slack at 80%)
- [x] [#374](https://github.com/deghosal-2026/hiveplane/issues/374) — M49-05 — Cost-per-completed-task: real cost including retries, failed loops, escalations, cache hits
- [x] [#375](https://github.com/deghosal-2026/hiveplane/issues/375) — M49-06 — Showback API + `hiveplane cost` CLI + spend view data
- [x] [#376](https://github.com/deghosal-2026/hiveplane/issues/376) — M49-07 — Tests: attribution accuracy, period rollover, alert at threshold, cost-per-completed-task includes retries

**Test ticket:** [#377](https://github.com/deghosal-2026/hiveplane/issues/377) — Test cases for Cost Showback, Teams/Tenants & Budget Periods/Alerts

**Deliverables:**
- `hiveplane.cost` extension (attribution, periods, alerts, cost-per-task)
- `docs/design/cost-roi-v2-design.md`

**Acceptance criteria:**
- [x] Spend is attributed by tenant → team → agent with no un-attributed usage in the field test
- [x] Budget periods roll over correctly and enforce per-period caps
- [x] A threshold crossing fires an alert
- [x] Cost-per-completed-task includes retries, failed loops, and escalations (verified by test)
- [x] The spend view and `hiveplane cost` agree with the metering ledger

**Done when:** every dollar is attributable and cost-per-outcome is a real, trustworthy number.

> **Status:** M49 complete. `hiveplane.cost` records append-only, fully attributed usage events (tenant → team → workload); an event missing attribution is dead-lettered and raises rather than silently defaulting. Budget periods (day/week/month) materialize per scope with a carry rule (`none`/`capped`/`full`), spend is applied per matching period, and threshold alerts fire exactly once per `(period, threshold)` at 50/80/100%. Tenant spend caps fail closed (`SpendCapExceededError`). Showback computes cost-per-completed-task including retries, escalations, and cache-hit savings, grouped by team or workload, and reports an `unattributed` count that must be zero. Ships API (`GET /cost/showback`, `GET /cost/showback/{tenant}/{team}`), CLI (`hiveplane cost showback`), and tables `budget_periods`/`budget_alerts` (migration `0026`). Issues #370–#377 closed; 2089 tests pass with a database, coverage 95.04%, ruff and mypy strict clean.

**Dependencies:** M25 (cost models, teams); v0.1.0 budget/usage accounting.

**Notes / risks:** attribution gaps create disputes — assert zero un-attributed usage. Cost-per-task must count wasted work, not just successful calls, or it lies.

## M50 — Cost Depth, Caching & ROI Dashboards

**Objective:** Add pre-admission cost estimates, model-tier routing, per-tenant spend caps, an attested result cache, a chargeback metering API, and fleet-wide ROI dashboards with forecasts and overrun prediction.

**Work items:**

- [x] [#378](https://github.com/deghosal-2026/hiveplane/issues/378) — M50-01 — Pre-admission cost estimate: expected cost by task type shown before admission (based on history)
- [x] [#379](https://github.com/deghosal-2026/hiveplane/issues/379) — M50-02 — Model-tier routing: cheap model in staging / strong model in prod, configurable per workload
- [x] [#380](https://github.com/deghosal-2026/hiveplane/issues/380) — M50-03 — Per-tenant spend caps with hard stop (admission denied when exceeded)
- [x] [#381](https://github.com/deghosal-2026/hiveplane/issues/381) — M50-04 — Result cache with attestation: same task + agent version + config → cached result; invalidated on re-cert
- [x] [#382](https://github.com/deghosal-2026/hiveplane/issues/382) — M50-05 — Cache accounting: hit rate + savings visible in showback
- [x] [#383](https://github.com/deghosal-2026/hiveplane/issues/383) — M50-06 — Chargeback metering API: usage endpoints for tenant billing (not just showback)
- [x] [#384](https://github.com/deghosal-2026/hiveplane/issues/384) — M50-07 — Budget analytics: burn forecasts + overrun prediction
- [x] [#385](https://github.com/deghosal-2026/hiveplane/issues/385) — M50-08 — Fleet ROI dashboards: spend vs. outcome, expensive-but-low-value flags, cost trends
- [x] [#386](https://github.com/deghosal-2026/hiveplane/issues/386) — M50-09 — Tests: estimate within tolerance; tier routing picks the right model; cap hard-stops; cache hit reuses + invalidates on re-cert; ROI flags correct

**Test ticket:** [#387](https://github.com/deghosal-2026/hiveplane/issues/387) — Test cases for Cost Depth, Caching & ROI Dashboards

**Deliverables:**
- Cost estimation/routing/caps/cache + metering API
- ROI dashboard views + `docs/design/cost-roi-v2-design.md`

**Acceptance criteria:**
- [x] Pre-admission estimates are within an agreed tolerance of actual cost
- [x] Model-tier routing selects the configured tier per environment
- [x] Exceeding a tenant spend cap hard-stops admission with a reason
- [x] A cache hit reuses a result and shows savings; re-certification invalidates the cache
- [x] The chargeback API returns usage per tenant for a period
- [x] ROI flags identify expensive-but-low-value agents with evidence

**Done when:** cost is predictable, controllable, billable, and connected to outcomes.

> **Status:** M50 complete. `hiveplane.cost.depth` adds pre-admission **cost estimates** (p50/p90 by workload + task type with sample-based confidence), **model-tier routing** (cheap in staging/sandbox, strong in production, per-workload override), a hard-stop **tenant spend cap** at admission (`tenant_spend_cap_exceeded`), an **attested result cache** keyed by task + workload + manifest version + bundle hash + config + tier (single attestation check, TTL, and re-cert invalidation) with hit-rate/savings accounting, **burn forecasts** with overrun probability, **fleet ROI** rows with evidence-backed `expensive_low_value` flags, and a **chargeback** export grouped by tenant/team/workload. Ships API (`GET /cost/estimates/{w}`, `POST /cost/cache/lookup|store`, `GET /cost/roi/fleet`, `GET /cost/forecast`, `GET /cost/metering/export`) and CLI (`hiveplane cost forecast|roi`). Issues #378–#387 closed; 2115 tests pass with a database, coverage 95.06%, ruff and mypy strict clean.

**Dependencies:** M49, M34 (re-cert invalidation), M42 (health/outcome).

**Notes / risks:** the result cache is only safe when inputs are fully deterministic and the agent version + config are part of the key — never cache across a re-cert. Spend caps must fail closed.

## Exit Gate (M49, M50)

[x] All tests in the system pass: `pytest`
[x] Code coverage total > 95%
[x] Ruff clean
[x] Mypy strict clean
[x] All relevant docs updated (showback, ROI, cost guide)
[x] All M49–M50 issues done and closed
[x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Cost & ROI theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar J
- [v0.2.0 index](wbs-v0.2.0-index.md)
