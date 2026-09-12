# WBS v0.1.0 — Part 4: Run Lifecycle & Execution API

**Milestones:** M8-M10 · **Issues:** #27-#31

## Goal

Accept runs only when registered, certified, permitted, and in budget. Move every run through one durable, observable state machine. Let operators intervene. Deliver results.

## M8 — Task Submission & Admission

**Issues:** [#27](https://github.com/deghosal-2026/hiveplane/issues/27)

- [ ] [#27](https://github.com/deghosal-2026/hiveplane/issues/27) — Task submission endpoint with admission checks

**Done when:** admission runs registry → certification → policy → budget; each failure returns a specific error; uncertified production submission is refused.

## M9 — State Machine & Persistence

**Issues:** [#28](https://github.com/deghosal-2026/hiveplane/issues/28) · [#29](https://github.com/deghosal-2026/hiveplane/issues/29)

- [ ] [#28](https://github.com/deghosal-2026/hiveplane/issues/28) — Run state machine and transition persistence
- [ ] [#29](https://github.com/deghosal-2026/hiveplane/issues/29) — Durable run state across control-plane restart

**Done when:** a full lifecycle event log exists, illegal transitions are rejected, and a paused run survives a restart with context intact.

## M10 — Intervention & Result Fan-out

**Issues:** [#30](https://github.com/deghosal-2026/hiveplane/issues/30) · [#31](https://github.com/deghosal-2026/hiveplane/issues/31)

- [ ] [#30](https://github.com/deghosal-2026/hiveplane/issues/30) — Intervention API (pause, resume, stop)
- [ ] [#31](https://github.com/deghosal-2026/hiveplane/issues/31) — Result fan-out service (Slack + webhook)

**Done when:** each action is audited with an actor; stop is immediate; pause is cooperative and honest; completion/failure/escalation fan out with a trace link and retries are recorded.

## Dependencies

- Part 2 (registry + certification status)
- Part 3 (certification gate)
- Part 5 (policy), Part 6 (budget) for full admission

## Exit Gate (M8, M9, M10)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Run lifecycle design](../../design/run-lifecycle-design.md)
- [Result fan-out design](../../design/result-fanout-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
