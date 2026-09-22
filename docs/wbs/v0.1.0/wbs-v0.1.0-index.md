# v0.1.0 — Work Breakdown Structure

**Goal:** Ship the certified control loop. Register agents, certify them against a reproducible benchmark, admit only certified agents to production, run them under budget/policy/sandbox, intervene on live runs, deliver results, and observe the fleet — all running locally on Docker Compose with three real workloads.

**Timeline:** 4-6 weeks

**Total scope:** 24 milestones · 112 tracked issues

> **Milestone exit gate:** Every milestone must pass the [exit gate](#milestone-exit-gate) before the next begins.
> **Issue tracking:** All work is tracked on GitHub. Milestones M1-M24 mirror the parts below; issues carry the `[0.1.0]` prefix and are grouped by milestone.
> **Progress:** Parts 1-11 complete and M22 complete (M1-M22, #2-#55, #82-#91) — 87/112 issues closed. M23 re-planned into phases P0-P4 (P2 split into ordered sub-phases P2a-P2d). **P0 (Design & Planning), P1 (LLM & Agent Enablement), P2a (Wiring fixes — the audit chain, #134-#139/#141/#142/#144), P2b (Corpus & provider reconciliation, #98/#123/#140), P2c (Real certification, #131/#129/#109), and P2d's #110 (subprocess sandbox + caps + channel) are done — all three example workloads certify at production threshold through the real adapter path, and sandboxed runs execute in a capped child.** **18 open issues remain** across P2d (hardening: #111, #122, #124, #130), P3 (Docker: #143, #93, #94, #95, #96, #59) and P4 (field test: #99, #121, #56, #57, #58, #100, #101, #102), plus M24. M23-M24 remain.

---

## Parts

| Part | Title | Milestones | Issues |
|------|-------|------------|--------|
| [1](wbs-v0.1.0-part1-foundation.md) | Foundation & Data Models | M1-M2 | [#2-#10](https://github.com/deghosal-2026/hiveplane/milestones?q=M1) |
| [2](wbs-v0.1.0-part2-registry.md) | Registry Service | M3-M4 | [#11-#18](https://github.com/deghosal-2026/hiveplane/issues) |
| [3](wbs-v0.1.0-part3-certification-pipeline.md) | Certification Pipeline **(the thesis)** | M5-M7 | [#19-#26](https://github.com/deghosal-2026/hiveplane/issues) |
| [4](wbs-v0.1.0-part4-run-lifecycle.md) | Run Lifecycle & Execution API | M8-M10 | [#27-#31](https://github.com/deghosal-2026/hiveplane/issues) |
| [5](wbs-v0.1.0-part5-policy-approvals.md) | Policy Engine & Approvals | M11-M12 | [#32-#35](https://github.com/deghosal-2026/hiveplane/issues) |
| [6](wbs-v0.1.0-part6-budget.md) | Budget Enforcement | M13 | [#36-#37](https://github.com/deghosal-2026/hiveplane/issues) |
| [7](wbs-v0.1.0-part7-execution-sandbox.md) | Execution Sandbox & Tool-Output Shaping | M14-M15 | [#38-#41](https://github.com/deghosal-2026/hiveplane/issues) |
| [8](wbs-v0.1.0-part8-adapters.md) | Runtime Adapters & Conformance | M16-M17 | [#42-#45](https://github.com/deghosal-2026/hiveplane/issues) |
| [9](wbs-v0.1.0-part9-state-store.md) | State Store & Persistence | M18 | [#46-#47](https://github.com/deghosal-2026/hiveplane/issues) |
| [10](wbs-v0.1.0-part10-telemetry.md) | Telemetry & Observability | M19-M20 | [#48-#51](https://github.com/deghosal-2026/hiveplane/issues) |
| [11](wbs-v0.1.0-part11-cli-operator-ui.md) | CLI & Operator UI | M21-M22 | [#52-#55](https://github.com/deghosal-2026/hiveplane/issues), [#82-#91](https://github.com/deghosal-2026/hiveplane/issues) |
| [12](wbs-v0.1.0-part12-field-test.md) | Field Test | M23 | [#56-#59, #92-#133](https://github.com/deghosal-2026/hiveplane/issues) |
| [13](wbs-v0.1.0-part13-release-readiness.md) | Release Readiness | M24 | [#60-#61](https://github.com/deghosal-2026/hiveplane/issues) |
| **Total** | | **M1-M24** | **112 issues** |

---

## Milestone Map

```
Part 1   Part 2    Part 3 (thesis)      Part 4        Part 5    Part 6  Part 7
M1 M2    M3 M4     M5  M6  M7           M8 M9 M10     M11 M12   M13     M14 M15
 │ │      │ │       │   │   │            │  │  │        │  │      │       │  │
 └─┴──────┴─┴───────┴───┴───┴────────────┴──┴──┴────────┴──┴──────┴───────┴──┘
        Foundation ─ Registry ─ Certification ─ Lifecycle ─ Policy ─ Budget ─ Sandbox
                                             │
Part 8   Part 9   Part 10   Part 11   Part 12   Part 13
M16 M17  M18      M19 M20   M21 M22   M23       M24
 │  │     │        │  │      │  │      │         │
 └──┴─────┴────────┴──┴──────┴──┴──────┴─────────┘
   Adapters ─ Store ─ Telemetry ─ CLI/UI ─ Field Test ─ Release
```

---

## Dependencies

```
Part 1 (Foundation)
  └─> Part 2 (Registry)
        └─> Part 3 (Certification) ──> Part 4 (Run Lifecycle)
                                          ├─> Part 5 (Policy & Approvals)
                                          ├─> Part 6 (Budget)
                                          ├─> Part 7 (Sandbox & Shaping)
                                          ├─> Part 8 (Adapters)
                                          └─> Part 9 (State Store)
Part 4 + 9 ─> Part 10 (Telemetry)
Part 4 + 10 ─> Part 11 (CLI & UI)
All ─> Part 12 (Field Test) ─> Part 13 (Release)
```

**Critical path:** Part 1 → Part 2 → Part 3 → Part 4 → Part 12 → Part 13. Certification gates everything downstream.

---

## Milestone Exit Gate

Every milestone (M1-M24) must pass this gate before the next milestone begins:

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%: `pytest --cov=src/hiveplane --cov-report=term-missing`
- [ ] Ruff clean: `ruff check`
- [ ] Mypy strict clean: `mypy src/ tests/`
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

---

## Final Release Gate (v0.1.0)

Before tagging v0.1.0, ALL of the following must be true:

- [ ] All M1-M24 milestones complete and exit gates passed
- [ ] Three real agents registered and certified via benchmark
- [ ] An uncertified agent is refused admission to a production context
- [ ] A seeded manifest change is blocked by re-certification (regression caught)
- [ ] Attestation is signed and verified on read
- [ ] A seeded model-swap attempt is blocked
- [ ] Budget enforcement blocks an over-budget run
- [ ] Execution isolation caps a destructive run
- [ ] Tool-output shaping truncates a large payload before it reaches the agent
- [ ] Operators can inspect and stop any run from one surface
- [ ] A result is fanned out to Slack and a generic webhook
- [ ] Audit trail is complete for every run in the field test
- [ ] Docker Compose stack starts with one command on macOS, Linux, and CI
- [ ] `hiveplane init` scaffolds a working project in under 5 minutes
- [ ] Lint strict clean, mypy strict, zero errors
- [ ] Test coverage total > 95%
- [ ] Documentation includes benchmark, corpus, and field-test methodology
- [ ] `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md` published
- [ ] v0.1.0 release notes published and GitHub release created
