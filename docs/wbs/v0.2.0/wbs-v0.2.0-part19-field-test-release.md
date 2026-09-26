# WBS v0.2.0 — Part 19: Field Test & Release Readiness

**Milestones:** M61–M62 · **Part:** 19 of 19

## Goal

Prove the Complete Fleet OS against a live stack — 25 field-test scenarios plus a concurrency load test — then ship it: migration guide, docs overhaul, status promotion, signed release, PyPI + Homebrew, and the final tag.

## M61 — Field Test (25 Scenarios + Load Test)

**Objective:** Exercise every pillar end-to-end against the live stack, with 25 scenarios and a load test sustaining ≥50 concurrent runs, producing a published field-test report.

**Work items:**

- [ ] [#476](https://github.com/deghosal-2026/hiveplane/issues/476) — M61-01 — Field-test plan: 25 scenarios mapping to the 34 release gates, with expected outcomes
- [ ] [#477](https://github.com/deghosal-2026/hiveplane/issues/477) — M61-02 — Scenario S1–S5: certification, promotion gate, regression diff, drift quarantine + reinstatement
- [ ] [#478](https://github.com/deghosal-2026/hiveplane/issues/478) — M61-03 — Scenario S6–S10: triggers (≥3 sources), pipelines, canary/auto-promote, shadow runs, agent-as-tool
- [ ] [#479](https://github.com/deghosal-2026/hiveplane/issues/479) — M61-04 — Scenario S11–S15: injection block + quarantine, context budget pause, spend-velocity pause, circuit breaker, egress denial
- [ ] [#480](https://github.com/deghosal-2026/hiveplane/issues/480) — M61-05 — Scenario S16–S20: secrets never leak, RBAC viewer denial, worker kill → lease reassign, preemption, DLQ replay
- [ ] [#481](https://github.com/deghosal-2026/hiveplane/issues/481) — M61-06 — Scenario S21–S25: showback + cost-per-task, cache hit + invalidation, GitOps reconciliation, synthetic probe, public attestation verify + kill switch
- [ ] [#482](https://github.com/deghosal-2026/hiveplane/issues/482) — M61-07 — Load test: sustain ≥50 concurrent runs; record throughput, latency, error rate, and resource use
- [ ] [#483](https://github.com/deghosal-2026/hiveplane/issues/483) — M61-08 — Load test on the Helm/k3d reference deployment (multi-worker)
- [ ] [#484](https://github.com/deghosal-2026/hiveplane/issues/484) — M61-09 — Field-test report generated from results (pass/fail per scenario + gate mapping) + Docker/container test track
- [ ] [#485](https://github.com/deghosal-2026/hiveplane/issues/485) — M61-10 — Fix all field-test findings; re-run until green

**Test ticket:** [#486](https://github.com/deghosal-2026/hiveplane/issues/486) — Test cases for Field Test (25 Scenarios + Load Test)

**Deliverables:**
- `docs/field-test/v0.2.0/` — plan, results, `FIELD_TEST_REPORT.md`, container-test report
- Load-test report with throughput numbers

**Acceptance criteria:**
- [ ] All 25 scenarios pass against the live stack
- [ ] Every one of the 34 release gates is demonstrated by at least one scenario
- [ ] The load test sustains ≥50 concurrent runs with documented throughput and no correctness failures
- [ ] Multi-worker execution is exercised on the k3d deployment
- [ ] The field-test report is generated from machine results (not hand-written)
- [ ] All findings are fixed and re-verified

**Done when:** the Complete Fleet OS passes 25/25 scenarios and the concurrency load test on a live stack.

**Dependencies:** all M25–M60 milestones.

**Notes / risks:** the field test is the release's evidence — do not soften scenarios to pass. Any gate that cannot be demonstrated blocks release. Budget real time for findings (v0.1.0 needed a dedicated fix cycle).

## M62 — Release Readiness & Docs Overhaul

**Objective:** Ship v0.2.0: complete the docs overhaul, write the migration guide, promote status alpha → beta, publish signed artifacts to PyPI + Homebrew, and tag the final big release.

**Work items:**

- [ ] [#487](https://github.com/deghosal-2026/hiveplane/issues/487) — M62-01 — Migration guide: v0.1.0 → v0.2.0 (schema, config, API changes, breaking changes)
- [ ] [#488](https://github.com/deghosal-2026/hiveplane/issues/488) — M62-02 — Docs overhaul: user guide, tutorials, architecture tour, operator runbook
- [ ] [#489](https://github.com/deghosal-2026/hiveplane/issues/489) — M62-03 — `CHANGELOG.md` v0.2.0 entry + release notes (`docs/release/v0.2.0/`)
- [ ] [#490](https://github.com/deghosal-2026/hiveplane/issues/490) — M62-04 — README refresh: Complete Fleet OS scope, badges (including OpenSSF), quick start
- [ ] [#491](https://github.com/deghosal-2026/hiveplane/issues/491) — M62-05 — Status promotion: alpha → beta across README, PyPI classifiers, docs
- [ ] [#492](https://github.com/deghosal-2026/hiveplane/issues/492) — M62-06 — Final release gate: run the full checklist (index → Final Release Gate) and confirm every item
- [ ] [#493](https://github.com/deghosal-2026/hiveplane/issues/493) — M62-07 — Publish to PyPI (`hiveplane==0.2.0`) + Homebrew; signed, SBOM'd, cosign-signed artifacts with provenance
- [ ] [#494](https://github.com/deghosal-2026/hiveplane/issues/494) — M62-08 — Tag `v0.2.0` and create the GitHub release (wheel + sdist + field-test report + SBOM + provenance)
- [ ] [#495](https://github.com/deghosal-2026/hiveplane/issues/495) — M62-09 — Article-series prep (5–6 posts: immune system, progressive delivery, showback/ROI, multi-tenant Helm, learning loop, `ask` copilot)
- [ ] [#496](https://github.com/deghosal-2026/hiveplane/issues/496) — M62-10 — Post-release: announce, and open the maintenance-mode backlog (docs/community/security)

**Test ticket:** [#497](https://github.com/deghosal-2026/hiveplane/issues/497) — Test cases for Release Readiness & Docs Overhaul

**Deliverables:**
- Migration guide, docs overhaul, release notes
- Published PyPI + Homebrew packages; signed artifacts
- Git tag `v0.2.0` + GitHub release
- Article drafts

**Acceptance criteria:**
- [ ] The full Final Release Gate checklist is green (all 34 gates + exit gates)
- [ ] `pip install hiveplane==0.2.0` and `brew install hiveplane` both work
- [ ] Released artifacts verify (cosign) and SBOM/provenance are published
- [ ] The migration guide covers every breaking change from v0.1.0
- [ ] Status is beta across all surfaces
- [ ] `v0.2.0` tag + GitHub release exist with attached artifacts

**Done when:** v0.2.0 — the Complete Fleet OS — is published, documented, and tagged; the project enters maintenance mode.

**Dependencies:** M61; all milestones.

**Notes / risks:** this is the last big release — do not rush the release gate. If a gate is red, fix it; do not tag. After release, the project shifts to maintenance (docs, community, articles, security patches).

## Exit Gate (M61, M62)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (migration, user guide, runbook, release notes, CHANGELOG)
- [ ] All M61–M62 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 07 Success Metrics](../../prd/07-success-metrics.md)
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars S, final release gate
- [v0.1.0 field test](../v0.1.0/wbs-v0.1.0-part12-field-test.md)
- [v0.2.0 index](wbs-v0.2.0-index.md)
