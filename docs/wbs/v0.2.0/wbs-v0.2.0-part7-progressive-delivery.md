# WBS v0.2.0 — Part 7: Progressive Delivery

**Milestones:** M37–M38 · **Part:** 7 of 19

## Goal

Bring canary/blue-green thinking to agents. Shadow runs compare a candidate against the certified version on real tasks; canary routing sends a fraction of live traffic to a candidate and auto-promotes on clean results; model experiment campaigns pick winners with benchmark evidence.

## M37 — Shadow Runs

**Objective:** Run a candidate workload side-by-side with the certified production version on the same inputs, without affecting production outcomes, and produce an outcome diff.

**Work items:**

- [x] [#262](https://github.com/deghosal-2026/hiveplane/issues/262) — M37-01 — Shadow execution: run a candidate workload on a mirrored task without delivering its result to the user/system
- [x] [#263](https://github.com/deghosal-2026/hiveplane/issues/263) — M37-02 — Pairing: shadow run is linked to the production run it mirrors; shared task input
- [x] [#264](https://github.com/deghosal-2026/hiveplane/issues/264) — M37-03 — Outcome diff: compare outputs, tool calls, cost, latency, and policy decisions between production and candidate
- [x] [#265](https://github.com/deghosal-2026/hiveplane/issues/265) — M37-04 — Isolation guarantees: shadow runs cannot trigger side-effecting tools (read-only tool policy by default)
- [x] [#266](https://github.com/deghosal-2026/hiveplane/issues/266) — M37-05 — Shadow budget: separate cost cap so shadowing never consumes production budget
- [x] [#267](https://github.com/deghosal-2026/hiveplane/issues/267) — M37-06 — Shadow report + API/CLI (`hiveplane shadow report`)
- [x] [#268](https://github.com/deghosal-2026/hiveplane/issues/268) — M37-07 — Tests: shadow run mirrors production, never delivers, respects isolation and its own budget

**Test ticket:** [#269](https://github.com/deghosal-2026/hiveplane/issues/269) — Test cases for Shadow Runs

**Deliverables:**
- `hiveplane.progressive.shadow` package + shadow report format
- `docs/design/progressive-delivery-design.md`

**Acceptance criteria:**
- [x] A shadow run executes the candidate on the same input as the paired production run
- [x] Shadow output is never delivered to any fan-out channel
- [x] Shadow runs cannot call destructive tools (verified by test)
- [x] Shadow spend is attributed separately and capped
- [x] The outcome diff surfaces meaningful differences (quality, cost, latency, tool calls)

**Done when:** operators can compare a candidate to production on real work without risk.

> **Status:** M37 complete. `hiveplane.progressive` shadow runs (`ShadowRun`, `ShadowService`, `RunShadowRunner`, outcome diff) land with migration `0019`; shadow runs mirror the paired production task, never fan out, run read-only (`Run.shadow_of`/`read_only`), and bill a separate capped shadow budget. API `POST /shadow`, `GET /shadow/{id}/report`; CLI `hiveplane shadow report`. Issues #262–#269 closed.

**Dependencies:** M32 (certification), M28 (trigger mirroring); v0.1.0 run lifecycle + policy.

**Notes / risks:** shadow side effects are the key hazard — default to read-only tools and hard-block writes. Keep shadow volume low to control cost.

## M38 — Canary Routing, Auto-Promote & Model Experiments

**Objective:** Route a configurable fraction of live triggers to a candidate version, evaluate it against the certified baseline, and auto-promote on clean results — plus run multi-config model experiment campaigns scored by the benchmark.

**Work items:**

- [x] [#270](https://github.com/deghosal-2026/hiveplane/issues/270) — M38-01 — Canary routing: percentage-based split of eligible triggers to a candidate version
- [x] [#271](https://github.com/deghosal-2026/hiveplane/issues/271) — M38-02 — Canary evaluation: compare candidate vs. baseline on live metrics + sampled judge scores over a window
- [x] [#272](https://github.com/deghosal-2026/hiveplane/issues/272) — M38-03 — Auto-promote: on clean canary (no regressions, metrics within tolerance), promote candidate and re-point traffic
- [x] [#273](https://github.com/deghosal-2026/hiveplane/issues/273) — M38-04 — Auto-abort: on regression/error-rate breach, roll back traffic and quarantine the candidate
- [x] [#274](https://github.com/deghosal-2026/hiveplane/issues/274) — M38-05 — Guardrails: minimum sample size, evaluation window, blast-radius cap, manual override
- [x] [#275](https://github.com/deghosal-2026/hiveplane/issues/275) — M38-06 — Model experiment campaigns: route across ≥2 model configs, benchmark-score each, select a winner
- [x] [#276](https://github.com/deghosal-2026/hiveplane/issues/276) — M38-07 — Canary/experiment state + audit (who/when/why promoted or aborted)
- [x] [#277](https://github.com/deghosal-2026/hiveplane/issues/277) — M38-08 — Tests: canary splits traffic, auto-promotes clean candidate, aborts on seeded regression, experiment picks the higher-scoring config

**Test ticket:** [#278](https://github.com/deghosal-2026/hiveplane/issues/278) — Test cases for Canary Routing, Auto-Promote & Model Experiments

**Deliverables:**
- `hiveplane.progressive.canary` package + campaign model
- API + CLI (`hiveplane canary start|status|promote|abort`)
- `docs/design/progressive-delivery-design.md`

**Acceptance criteria:**
- [x] Canary routes exactly the configured fraction of eligible triggers to the candidate
- [x] A clean canary auto-promotes and traffic re-points without manual action
- [x] A seeded regression during canary auto-aborts and rolls back
- [x] Minimum-sample and blast-radius guardrails prevent premature promotion
- [x] A model experiment selects the benchmark-best config with recorded evidence

**Done when:** agent versions roll out progressively with evidence-based auto-promotion and safe auto-abort.

> **Status:** M38 complete. Canary routing/evaluation/auto-promote/auto-abort with min-sample, blast-radius, and manual-override guardrails, plus multi-arm model experiments (`CanaryService`, `ExperimentService`; migration `0020`). API `/canary*`, `/experiments*`; CLI `hiveplane canary ...`, `hiveplane experiment start`. Every transition is audited. Issues #270–#278 closed.

**Dependencies:** M37; M33 (diff), M34 (quarantine/rollback).

**Notes / risks:** auto-promote is high-trust automation — require a clean evaluation window and make override always available. Never auto-promote on insufficient sample size.

## Exit Gate (M37, M38)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (shadow runs, progressive delivery, user guide)
- [x] All M37–M38 issues done and closed
- [x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Certification Pipeline theme (progressive delivery)
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar D
- [v0.2.0 index](wbs-v0.2.0-index.md)
