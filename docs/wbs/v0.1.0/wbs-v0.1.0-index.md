# v0.1.0 — Work Breakdown Structure

**Goal:** Prove the control loop with real workloads. Register agents, submit tasks, persist run state, enforce budgets, pause/resume/cancel from an operator API, and export traces and metrics — all running locally on Docker Compose.

**Timeline:** 2-3 weeks

## Parts

| Part | Title | Milestones | Tasks |
|------|-------|------------|-------|
| [1](wbs-v0.1.0-part1-foundation.md) | Foundation & Data Models | M1-M2 | TBD |
| [2](wbs-v0.1.0-part2-registry.md) | Registry Service | M3-M4 | TBD |
| [3](wbs-v0.1.0-part3-run-lifecycle.md) | Run Lifecycle & Execution API | M5-M7 | TBD |
| [4](wbs-v0.1.0-part4-policy.md) | Policy Engine & Approvals | M8-M9 | TBD |
| [5](wbs-v0.1.0-part5-budget.md) | Budget Enforcement | M10 | TBD |
| [6](wbs-v0.1.0-part6-adapters.md) | Runtime Adapters | M11-M12 | TBD |
| [7](wbs-v0.1.0-part7-state-store.md) | State Store & Persistence | M13 | TBD |
| [8](wbs-v0.1.0-part8-telemetry.md) | Telemetry & Observability | M14-M15 | TBD |
| [9](wbs-v0.1.0-part9-operator-ui-cli.md) | Operator UI & CLI | M16-M17 | TBD |
| [10](wbs-v0.1.0-part10-field-test.md) | Field Test | M18 | TBD |
| [11](wbs-v0.1.0-part11-release-readiness.md) | Release Readiness | M19 | TBD |
| **Total** | | **M1-M19** | **TBD** |

## Milestone Map

```
M1  M2  M3  M4  M5  M6  M7  M8  M9  M10 M11 M12 M13 M14 M15 M16 M17 M18 M19
 │   │   │   │   │   │   │   │   │   │   │   │   │   │   │   │   │   │   │
 └───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┘
 Part1    Part2        Part3          P4   P5    Part6     P7   P8     P9  P10 P11
```

## Dependencies

```
Part 1 (Foundation) ──> Part 2 (Registry) ──> Part 3 (Run Lifecycle)
Part 3 ──> Part 4 (Policy)
Part 3 ──> Part 5 (Budget)
Part 2, 3 ──> Part 6 (Adapters)
Part 3 ──> Part 7 (State Store)
Part 3, 7 ──> Part 8 (Telemetry)
Part 2, 3, 8 ──> Part 9 (UI & CLI)
All ──> Part 10 (Field Test)
Part 10 ──> Part 11 (Release Readiness)
```

## Exit Gate (per milestone)

Every milestone must pass its exit gate before the next begins:

- [ ] Run all tests: `pytest` — all pass
- [ ] Lint strict clean: `ruff check` + `mypy src/` — zero errors
- [ ] Update all docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit with message: `milestone: M{n} complete`
- [ ] Push to main

## Final Release Gate (v0.1.0)

Before tagging v0.1.0, ALL of the following must be true:

- [ ] All M1-M19 milestones complete and exit gates passed
- [ ] Three real agents run through the same lifecycle
- [ ] Operators can inspect and stop any run from one surface
- [ ] Budget enforcement blocks a seeded over-budget run
- [ ] Audit trail complete for every run in the field test
- [ ] Docker Compose stack starts with one command
- [ ] Lint strict clean, mypy strict, zero errors
- [ ] `README.md`, `USER_GUIDE.md`, `ADAPTERS.md`, `observability.md` accurate
- [ ] v0.1.0 release notes published
