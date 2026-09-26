# WBS v0.2.0 — Part 13: Cost, Showback & ROI

**Milestones:** M49–M50 · **Part:** 13 of 19

## Goal

Make cost legible and controllable. Attribute spend by tenant → team → agent, compute true cost-per-completed-task, forecast and cap spend, route by model tier, cache results, expose metering for billing, and surface fleet ROI.

## M49 — Cost Showback, Teams/Tenants & Budget Periods/Alerts

**Objective:** Attribute spend accurately across the fleet, make teams/tenants first-class, add budget periods with threshold alerts, and expose cost-per-completed-task.

**Work items:**

- [ ] [#370](https://github.com/deghosal-2026/hiveplane/issues/370) — M49-01 — Cost attribution: every model/tool usage event tagged with tenant, team, agent, run, pipeline
- [ ] [#371](https://github.com/deghosal-2026/hiveplane/issues/371) — M49-02 — Teams/tenants as first-class entities (org model + membership + attribution)
- [ ] [#372](https://github.com/deghosal-2026/hiveplane/issues/372) — M49-03 — Budget periods: day/week/month buckets per tenant/team/agent with carry rules
- [ ] [#373](https://github.com/deghosal-2026/hiveplane/issues/373) — M49-04 — Threshold alerts: notify at configurable percentages (e.g., Slack at 80%)
- [ ] [#374](https://github.com/deghosal-2026/hiveplane/issues/374) — M49-05 — Cost-per-completed-task: real cost including retries, failed loops, escalations, cache hits
- [ ] [#375](https://github.com/deghosal-2026/hiveplane/issues/375) — M49-06 — Showback API + `hiveplane cost` CLI + spend view data
- [ ] [#376](https://github.com/deghosal-2026/hiveplane/issues/376) — M49-07 — Tests: attribution accuracy, period rollover, alert at threshold, cost-per-completed-task includes retries

**Test ticket:** [#377](https://github.com/deghosal-2026/hiveplane/issues/377) — Test cases for Cost Showback, Teams/Tenants & Budget Periods/Alerts

**Deliverables:**
- `hiveplane.cost` extension (attribution, periods, alerts, cost-per-task)
- `docs/design/cost-roi-v2-design.md`

**Acceptance criteria:**
- [ ] Spend is attributed by tenant → team → agent with no un-attributed usage in the field test
- [ ] Budget periods roll over correctly and enforce per-period caps
- [ ] A threshold crossing fires an alert
- [ ] Cost-per-completed-task includes retries, failed loops, and escalations (verified by test)
- [ ] The spend view and `hiveplane cost` agree with the metering ledger

**Done when:** every dollar is attributable and cost-per-outcome is a real, trustworthy number.

**Dependencies:** M25 (cost models, teams); v0.1.0 budget/usage accounting.

**Notes / risks:** attribution gaps create disputes — assert zero un-attributed usage. Cost-per-task must count wasted work, not just successful calls, or it lies.

## M50 — Cost Depth, Caching & ROI Dashboards

**Objective:** Add pre-admission cost estimates, model-tier routing, per-tenant spend caps, an attested result cache, a chargeback metering API, and fleet-wide ROI dashboards with forecasts and overrun prediction.

**Work items:**

- [ ] [#378](https://github.com/deghosal-2026/hiveplane/issues/378) — M50-01 — Pre-admission cost estimate: expected cost by task type shown before admission (based on history)
- [ ] [#379](https://github.com/deghosal-2026/hiveplane/issues/379) — M50-02 — Model-tier routing: cheap model in staging / strong model in prod, configurable per workload
- [ ] [#380](https://github.com/deghosal-2026/hiveplane/issues/380) — M50-03 — Per-tenant spend caps with hard stop (admission denied when exceeded)
- [ ] [#381](https://github.com/deghosal-2026/hiveplane/issues/381) — M50-04 — Result cache with attestation: same task + agent version + config → cached result; invalidated on re-cert
- [ ] [#382](https://github.com/deghosal-2026/hiveplane/issues/382) — M50-05 — Cache accounting: hit rate + savings visible in showback
- [ ] [#383](https://github.com/deghosal-2026/hiveplane/issues/383) — M50-06 — Chargeback metering API: usage endpoints for tenant billing (not just showback)
- [ ] [#384](https://github.com/deghosal-2026/hiveplane/issues/384) — M50-07 — Budget analytics: burn forecasts + overrun prediction
- [ ] [#385](https://github.com/deghosal-2026/hiveplane/issues/385) — M50-08 — Fleet ROI dashboards: spend vs. outcome, expensive-but-low-value flags, cost trends
- [ ] [#386](https://github.com/deghosal-2026/hiveplane/issues/386) — M50-09 — Tests: estimate within tolerance; tier routing picks the right model; cap hard-stops; cache hit reuses + invalidates on re-cert; ROI flags correct

**Test ticket:** [#387](https://github.com/deghosal-2026/hiveplane/issues/387) — Test cases for Cost Depth, Caching & ROI Dashboards

**Deliverables:**
- Cost estimation/routing/caps/cache + metering API
- ROI dashboard views + `docs/design/cost-roi-v2-design.md`

**Acceptance criteria:**
- [ ] Pre-admission estimates are within an agreed tolerance of actual cost
- [ ] Model-tier routing selects the configured tier per environment
- [ ] Exceeding a tenant spend cap hard-stops admission with a reason
- [ ] A cache hit reuses a result and shows savings; re-certification invalidates the cache
- [ ] The chargeback API returns usage per tenant for a period
- [ ] ROI flags identify expensive-but-low-value agents with evidence

**Done when:** cost is predictable, controllable, billable, and connected to outcomes.

**Dependencies:** M49, M34 (re-cert invalidation), M42 (health/outcome).

**Notes / risks:** the result cache is only safe when inputs are fully deterministic and the agent version + config are part of the key — never cache across a re-cert. Spend caps must fail closed.

## Exit Gate (M49, M50)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (showback, ROI, cost guide)
- [ ] All M49–M50 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Cost & ROI theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar J
- [v0.2.0 index](wbs-v0.2.0-index.md)
