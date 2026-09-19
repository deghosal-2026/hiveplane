# WBS v0.1.0 — Part 11: CLI & Operator UI

**Milestones:** M21-M22 · **Issues:** #52-#55

## Goal

Give operators one surface to see the fleet, inspect a run, certify agents, act on approvals, and review spend.

## M21 — CLI

**Issues:** [#52](https://github.com/deghosal-2026/hiveplane/issues/52) · [#53](https://github.com/deghosal-2026/hiveplane/issues/53)

- [x] [#52](https://github.com/deghosal-2026/hiveplane/issues/52) — Core CLI commands (register, submit, runs, approvals)
- [x] [#53](https://github.com/deghosal-2026/hiveplane/issues/53) — Certification, trigger, and tools CLI + `init`

**Done when:** the full loop is driveable from the CLI; `hiveplane init` produces a working project with example workloads and a sample corpus; certify works end-to-end from the CLI.

## M22 — Operator UI

**Issues:** [#54](https://github.com/deghosal-2026/hiveplane/issues/54) · [#55](https://github.com/deghosal-2026/hiveplane/issues/55) · work items [#82-#91](https://github.com/deghosal-2026/hiveplane/issues?q=milestone%3AM22)

**Implementation design:** [Operator UI implementation](../../design/operator-ui-implementation.md)

Acceptance issues (what "done" means):

- [x] [#54](https://github.com/deghosal-2026/hiveplane/issues/54) — Fleet list and run detail screens
- [x] [#55](https://github.com/deghosal-2026/hiveplane/issues/55) — Approval queue, certification dashboard, and spend view

Work items (build order):

- [x] [#82](https://github.com/deghosal-2026/hiveplane/issues/82) — Spend read API (`GET /spend`)
- [x] [#83](https://github.com/deghosal-2026/hiveplane/issues/83) — Operator UI foundation (package, config, entrypoint, compose)
- [x] [#84](https://github.com/deghosal-2026/hiveplane/issues/84) — Operator UI control-plane HTTP client
- [x] [#85](https://github.com/deghosal-2026/hiveplane/issues/85) — Operator UI view models
- [x] [#86](https://github.com/deghosal-2026/hiveplane/issues/86) — Fleet and run detail screens (delivers #54)
- [x] [#87](https://github.com/deghosal-2026/hiveplane/issues/87) — Approval queue, certification dashboard, and spend screens (delivers #55)
- [x] [#88](https://github.com/deghosal-2026/hiveplane/issues/88) — Operator UI docs and M22 exit gate
- [x] [#89](https://github.com/deghosal-2026/hiveplane/issues/89) — API test cases for the operator-UI surface
- [x] [#90](https://github.com/deghosal-2026/hiveplane/issues/90) — UI test cases (client, views, routes)
- [x] [#91](https://github.com/deghosal-2026/hiveplane/issues/91) — Operator UI end-to-end tests with Playwright

**Done when:** the fleet is readable at a glance; run detail renders the execution story; approvals are resolvable from the UI; the certification dashboard and spend view render real data.

## Dependencies

- Part 4 (run lifecycle)
- Part 10 (telemetry)
- Part 3 (certification data)

## Exit Gate (M21, M22)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] Update all relevant docs affected by this milestone
- [x] Verify all issues in this milestone are done
- [x] Close all completed issues
- [x] Commit and push changes

## See Also

- [Operator UI design](../../design/operator-ui-design.md)
- [User guide](../../USER_GUIDE.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
