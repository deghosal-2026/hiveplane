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

**Issues:** [#54](https://github.com/deghosal-2026/hiveplane/issues/54) · [#55](https://github.com/deghosal-2026/hiveplane/issues/55)

- [ ] [#54](https://github.com/deghosal-2026/hiveplane/issues/54) — Fleet list and run detail screens
- [ ] [#55](https://github.com/deghosal-2026/hiveplane/issues/55) — Approval queue, certification dashboard, and spend view

**Done when:** the fleet is readable at a glance; run detail renders the execution story; approvals are resolvable from the UI; the certification dashboard and spend view render real data.

## Dependencies

- Part 4 (run lifecycle)
- Part 10 (telemetry)
- Part 3 (certification data)

## Exit Gate (M21, M22)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Operator UI design](../../design/operator-ui-design.md)
- [User guide](../../USER_GUIDE.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
