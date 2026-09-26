# WBS v0.2.0 — Part 9: Runtime Guards & Agent Health

**Milestones:** M41–M42 · **Part:** 9 of 19

## Goal

Add the fourth budget (context) and the guards that keep runaway spend and failures from spreading, then make agent health a first-class fleet signal: readiness, failure rate, MTTR, SLO/error budget, and burn-rate-driven throttling.

## M41 — Context-Window Budgets, Spend-Velocity Guards, Retries & Circuit Breakers

**Objective:** Enforce a per-run context-window budget live, detect spend-velocity anomalies, add retry policies with backoff, and trip/recover per-tool and per-workload circuit breakers.

**Work items:**

- [ ] [#297](https://github.com/deghosal-2026/hiveplane/issues/297) — M41-01 — Context-window budget: per-run max-context limit enforced live; per-step context accounting
- [ ] [#298](https://github.com/deghosal-2026/hiveplane/issues/298) — M41-02 — Breach behavior: pause the run cleanly (not crash) and record the accounting at breach
- [ ] [#299](https://github.com/deghosal-2026/hiveplane/issues/299) — M41-03 — Spend-velocity guards: burn-rate anomaly detection (e.g., $X in Y minutes) → auto-pause + alert
- [ ] [#300](https://github.com/deghosal-2026/hiveplane/issues/300) — M41-04 — Retry policies: per-workload/tool retry with exponential backoff, jitter, and max attempts
- [ ] [#301](https://github.com/deghosal-2026/hiveplane/issues/301) — M41-05 — Circuit breakers: per-tool and per-workload; trip on failure rate, half-open probe, recover
- [ ] [#302](https://github.com/deghosal-2026/hiveplane/issues/302) — M41-06 — Breaker/trip events audited and surfaced in the run story + health
- [ ] [#303](https://github.com/deghosal-2026/hiveplane/issues/303) — M41-07 — Guards integrate with policy (context/velocity breaches are policy decisions with reasons)
- [ ] [#304](https://github.com/deghosal-2026/hiveplane/issues/304) — M41-08 — Tests: context breach pauses cleanly; velocity guard pauses; breaker trips and recovers; retries honor backoff

**Test ticket:** [#305](https://github.com/deghosal-2026/hiveplane/issues/305) — Test cases for Context-Window Budgets, Spend-Velocity Guards, Retries & Circuit Breakers

**Deliverables:**
- `hiveplane.guards` package (context budget, velocity, retry, breaker)
- `docs/design/runtime-guards-design.md` and `docs/design/runtime-guards-design.md`

**Acceptance criteria:**
- [ ] Exceeding the context budget pauses the run with accounting shown (no crash, no silent truncation)
- [ ] A spend-velocity anomaly pauses the run and alerts the owner
- [ ] A circuit breaker trips after the configured failure threshold and recovers after a successful probe
- [ ] Retries apply backoff and stop at max attempts
- [ ] All guard activations carry a reason and are audited

**Done when:** context, spend velocity, and repeated failures are all governed at runtime with clean, explainable interventions.

**Dependencies:** v0.1.0 budget + sandbox; M40 (policy context).

**Notes / risks:** context accounting must reflect the real token counts from the provider boundary. Breakers must avoid flapping — use a half-open probe.

## M42 — Agent Health Model, SLO/Error Budget & Burn Throttle

**Objective:** Make health a first-class fleet signal — readiness, recent failure rate, MTTR, per-workload SLOs, and error-budget burn — and automatically throttle or quarantine when burn-through occurs.

**Work items:**

- [ ] [#306](https://github.com/deghosal-2026/hiveplane/issues/306) — M42-01 — Health model: readiness, recent failure rate, MTTR, drift status, and quality score per workload
- [ ] [#307](https://github.com/deghosal-2026/hiveplane/issues/307) — M42-02 — SLO hooks: per-workload availability + quality SLOs defined in the manifest
- [ ] [#308](https://github.com/deghosal-2026/hiveplane/issues/308) — M42-03 — Error-budget accounting: consumption from real failures/quality dips
- [ ] [#309](https://github.com/deghosal-2026/hiveplane/issues/309) — M42-04 — Burn-rate monitoring: fast/slow burn windows with alert thresholds
- [ ] [#310](https://github.com/deghosal-2026/hiveplane/issues/310) — M42-05 — Burn-through action: auto-throttle or quarantine via the shared immune-system machinery (M34)
- [ ] [#311](https://github.com/deghosal-2026/hiveplane/issues/311) — M42-06 — Health API + `hiveplane health` CLI + health dashboard data
- [ ] [#312](https://github.com/deghosal-2026/hiveplane/issues/312) — M42-07 — Integration: drift (M34) and online eval (M36) feed the health model
- [ ] [#313](https://github.com/deghosal-2026/hiveplane/issues/313) — M42-08 — Tests: readiness flips correctly; SLO burn computed; burn-through throttles/quarantines; MTTR computed from real events

**Test ticket:** [#314](https://github.com/deghosal-2026/hiveplane/issues/314) — Test cases for Agent Health Model, SLO/Error Budget & Burn Throttle

**Deliverables:**
- `hiveplane.health` package (model, SLO, error budget, burn)
- Health API + dashboard view + `docs/design/agent-health-slo-design.md` (update)

**Acceptance criteria:**
- [ ] The health dashboard shows readiness, failure rate, SLO status, burn, drift, and quality score
- [ ] An SLO burn-through triggers throttle or quarantine with a reason
- [ ] MTTR is computed from real failure/recovery events
- [ ] Health responds to seeded failure and recovery within the configured windows
- [ ] Health signals are queryable per workload and fleet-wide

**Done when:** every agent's health is visible and actionable, and burn-through is handled automatically.

**Dependencies:** M34 (quarantine), M36 (quality scores), M41 (guards).

**Notes / risks:** SLO targets that are too tight will cause constant throttling — ship sensible defaults and make them configurable. Avoid alert storms by debouncing.

## Exit Gate (M41, M42)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (context budget, breakers, agent health, SLO guide)
- [ ] All M41–M42 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Safe Execution, Observability & Health themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar F
- [v0.2.0 index](wbs-v0.2.0-index.md)
