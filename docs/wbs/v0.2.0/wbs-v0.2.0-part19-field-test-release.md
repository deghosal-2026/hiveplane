# WBS v0.2.0 — Part 19: Field Test & Release Readiness

**Milestones:** M61–M62 · **Part:** 19 of 19

## Goal

Prove the Complete Fleet OS against a live stack — 25 field-test scenarios plus a concurrency load test — then ship it: migration guide, docs overhaul, status promotion, signed release, PyPI + Homebrew, and the final tag.

## M61 — Field Test (25 Scenarios + Load Test)

**Objective:** Exercise every pillar end-to-end against the live stack, with the scenario suite
(**S1–S31 + H1–H5**, 36 scenarios) and a load test sustaining ≥50 concurrent runs, producing a
published field-test report.

**Work items:**

- [x] [#476](https://github.com/deghosal-2026/hiveplane/issues/476) — M61-01 — Field-test plan: 25 scenarios mapping to the 34 release gates, with expected outcomes
- [x] [#477](https://github.com/deghosal-2026/hiveplane/issues/477) — M61-02 — Scenario S1–S5: certification, promotion gate, regression diff, drift quarantine + reinstatement
- [x] [#478](https://github.com/deghosal-2026/hiveplane/issues/478) — M61-03 — Scenario S6–S10: triggers (≥3 sources), pipelines, canary/auto-promote, shadow runs, agent-as-tool
- [x] [#479](https://github.com/deghosal-2026/hiveplane/issues/479) — M61-04 — Scenario S11–S15: injection block + quarantine, context budget pause, spend-velocity pause, circuit breaker, egress denial
- [x] [#480](https://github.com/deghosal-2026/hiveplane/issues/480) — M61-05 — Scenario S16–S20: secrets never leak, RBAC viewer denial, worker kill → lease reassign, preemption, DLQ replay
- [x] [#481](https://github.com/deghosal-2026/hiveplane/issues/481) — M61-06 — Scenario S21–S25: showback + cost-per-task, cache hit + invalidation, GitOps reconciliation, synthetic probe, public attestation verify + kill switch
- [x] [#482](https://github.com/deghosal-2026/hiveplane/issues/482) — M61-07 — Load test: sustain ≥50 concurrent runs; record throughput, latency, error rate, and resource use
- [x] [#483](https://github.com/deghosal-2026/hiveplane/issues/483) — M61-08 — Load test on the Helm/k3d reference deployment (multi-worker)
- [x] [#484](https://github.com/deghosal-2026/hiveplane/issues/484) — M61-09 — Field-test report generated from results (pass/fail per scenario + gate mapping) + Docker/container test track
- [x] [#485](https://github.com/deghosal-2026/hiveplane/issues/485) — M61-10 — Fix all field-test findings; re-run until green

**Test ticket:** [#486](https://github.com/deghosal-2026/hiveplane/issues/486) — Test cases for Field Test (25 Scenarios + Load Test)

**Hardening & additions (M61-11…M61-41):** the track grew beyond M61-01..10 — the suite now has
**S1–S31 + H1–H5** (36 scenarios), a single v0.2.0 runner (`scripts/field-test-v02.sh`,
`scripts/field_test_runner_v02.py`), and an `--auth`/bootstrap pass. These follow-on items track
the remaining work.

- [x] [#580](https://github.com/deghosal-2026/hiveplane/issues/580) — M61-11 — Harden behavioral-depth scenarios (S12/S13/S19); split into #602/#603/#606
- [x] [#581](https://github.com/deghosal-2026/hiveplane/issues/581) — M61-12 — Deep-probe certification benchmark runs
- [x] [#582](https://github.com/deghosal-2026/hiveplane/issues/582) — M61-13 — Auth pass: assert 429 rate-limit (gate 34)
- [x] [#583](https://github.com/deghosal-2026/hiveplane/issues/583) — M61-14 — Priced-model field-test profile (live budget enforcement)
- [x] [#584](https://github.com/deghosal-2026/hiveplane/issues/584) — M61-15 — Final full sweep: republish evidence and reconcile report
- [x] [#585](https://github.com/deghosal-2026/hiveplane/issues/585) — M61-16 — Workload undeploy/cascade for delete-with-runs
- [x] [#586](https://github.com/deghosal-2026/hiveplane/issues/586) — M61-17 — Harness robustness: DLQ ordering + response-shape assumptions
- [x] [#587](https://github.com/deghosal-2026/hiveplane/issues/587) — M61-18 — CI hermetic replay sweep for the control loop
- [x] [#588](https://github.com/deghosal-2026/hiveplane/issues/588) — M61-19 — Document auth bootstrap & key rotation
- [x] [#589](https://github.com/deghosal-2026/hiveplane/issues/589) — M61-20 — Multi-tenant resource naming: resolve global-key limitation
- [x] [#590](https://github.com/deghosal-2026/hiveplane/issues/590) — M61-21 — Enrich the report renderer / prevent curated-report drift
- [x] [#591](https://github.com/deghosal-2026/hiveplane/issues/591) — M61-22 — Gate coverage: every gate must name an asserting scenario
- [x] [#592](https://github.com/deghosal-2026/hiveplane/issues/592) — M61-23 — Negative-path field scenarios
- [x] [#593](https://github.com/deghosal-2026/hiveplane/issues/593) — M61-24 — Fold field-test hardening rules into field-test-plan.md
- [x] [#594](https://github.com/deghosal-2026/hiveplane/issues/594) — M61-25 — Include the Docker `--auth` (L8) pass in the reports
- [x] [#595](https://github.com/deghosal-2026/hiveplane/issues/595) — M61-26 — Evidence-contract test: every scenario dir must contain required artifacts
- [x] [#596](https://github.com/deghosal-2026/hiveplane/issues/596) — M61-27 — Docker track: load test + UI on a multi-worker stack (M61-08 companion)
- [x] [#597](https://github.com/deghosal-2026/hiveplane/issues/597) — M61-28 — Update WBS part19 + milestone with the v0.2.0 field-test additions
- [x] [#598](https://github.com/deghosal-2026/hiveplane/issues/598) — M61-29 — Harden S4: seeded drift must AUTO-quarantine
- [x] [#599](https://github.com/deghosal-2026/hiveplane/issues/599) — M61-30 — Harden S8: canary must route 10% and auto-promote
- [x] [#602](https://github.com/deghosal-2026/hiveplane/issues/602) — M61-33 — Harden S12: context-budget overflow must pause the run
- [x] [#603](https://github.com/deghosal-2026/hiveplane/issues/603) — M61-34 — Harden S13: spend-velocity breach must pause the run
- [x] [#605](https://github.com/deghosal-2026/hiveplane/issues/605) — M61-36 — Harden S18: lease-EXPIRY reclaim path
- [x] [#606](https://github.com/deghosal-2026/hiveplane/issues/606) — M61-37 — Harden S19: real urgent-run preemption with attribution
- [x] [#608](https://github.com/deghosal-2026/hiveplane/issues/608) — M61-39 — Harden S23: GitOps deregister on delete + re-cert on threshold change
- [x] [#609](https://github.com/deghosal-2026/hiveplane/issues/609) — M61-40 — Harden S24: synthetic probe must FLAG decay before drift trips
- [x] [#610](https://github.com/deghosal-2026/hiveplane/issues/610) — M61-41 — Harden S25: tampered bundle must FAIL ADMISSION on provenance mismatch

**Deliverables:**
- `docs/field-test/v0.2.0/` — plan, results, `FIELD_TEST_REPORT.md`, container-test report
- Load-test report with throughput numbers

**Acceptance criteria:**
- [x] All 25 scenarios pass against the live stack
- [x] Every one of the 34 release gates is demonstrated by at least one scenario
- [x] The load test sustains ≥50 concurrent runs with documented throughput and no correctness failures
- [x] Multi-worker execution is exercised on the k3d deployment (M61-08, closed)
- [x] The field-test report is generated from machine results (not hand-written)
- [x] All findings are fixed and re-verified

**Done when:** the Complete Fleet OS passes 36/36 scenarios (S1–S31 + H1–H5) and the concurrency
load test on a live stack, with the defect ledger (D-1…D-11) referenced from
`docs/field-test/v0.2.0/FIELD_TEST_REPORT.md`.

**Status (2026-10-01):** Docker container track **green — 67/67 pass, 0 fail, 0 error, 0 skip** per `docs/field-test/v0.2.0/DOCKER_TEST_REPORT.md`. The four original failures are resolved and re-verified: **S4** (stale image; the already-committed sandbox re-cert exemption was absent from the running image), **S11** (test-fixture bug — used a disallowed tool so output injection scanning was never reached), **S7** (product bug — `RunNodeExecutor.submit` created pipeline child runs but never started them, so pipelines never completed), and **fan-out** (product bug — terminal fan-out recorded to `fan_out_deliveries` but had no API surface). Fixes in commit `c47d08d`: `src/hiveplane/pipelines/executor.py` starts the queued child; `src/hiveplane/api/runs.py` adds `GET /runs/{run_id}/deliveries`; each has a regression test. Load test (≥50 concurrent) passes. All M61 issues, including **M61-08 (k3d multi-worker load test)**, are closed.

**Dependencies:** all M25–M60 milestones.

**Notes / risks:** the field test is the release's evidence — do not soften scenarios to pass. Any gate that cannot be demonstrated blocks release. Budget real time for findings (v0.1.0 needed a dedicated fix cycle).

## M62 — Release Readiness & Docs Overhaul

**Objective:** Ship v0.2.0: complete the docs overhaul, write the migration guide, promote status alpha → beta, publish signed artifacts to PyPI + Homebrew, and tag the final big release.

**Work items:**

- [x] [#487](https://github.com/deghosal-2026/hiveplane/issues/487) — M62-01 — Migration guide: v0.1.0 → v0.2.0 (schema, config, API changes, breaking changes)
- [x] [#488](https://github.com/deghosal-2026/hiveplane/issues/488) — M62-02 — Docs overhaul: user guide, tutorials, architecture tour, operator runbook
- [x] [#489](https://github.com/deghosal-2026/hiveplane/issues/489) — M62-03 — `CHANGELOG.md` v0.2.0 entry + release notes (`docs/release/v0.2.0/`)
- [x] [#490](https://github.com/deghosal-2026/hiveplane/issues/490) — M62-04 — README refresh: Complete Fleet OS scope, badges (including OpenSSF), quick start
- [x] [#491](https://github.com/deghosal-2026/hiveplane/issues/491) — M62-05 — Status promotion: alpha → beta across README, PyPI classifiers, docs
- [x] [#492](https://github.com/deghosal-2026/hiveplane/issues/492) — M62-06 — Final release gate: run the full checklist (index → Final Release Gate) and confirm every item
- [x] [#493](https://github.com/deghosal-2026/hiveplane/issues/493) — M62-07 — Publish to PyPI (`hiveplane==0.2.0`) + Homebrew; signed, SBOM'd, cosign-signed artifacts with provenance
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
- [x] `pip install hiveplane==0.2.0` works (published to PyPI; Homebrew skipped by decision)
- [ ] Released artifacts verify (cosign) and SBOM/provenance are published
- [x] The migration guide covers every breaking change from v0.1.0
- [x] Status is beta across all surfaces
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
- [x] Commit and push changes

## See Also

- [PRD 07 Success Metrics](../../prd/07-success-metrics.md)
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars S, final release gate
- [v0.1.0 field test](../v0.1.0/wbs-v0.1.0-part12-field-test.md)
- [v0.2.0 index](wbs-v0.2.0-index.md)
