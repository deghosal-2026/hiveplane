# WBS v0.1.0 — Part 6: Budget Enforcement

**Milestone:** M13 · **Issues:** #36-#37

## Goal

Stop spend during execution, not after. Price usage accurately and enforce per-run and per-day limits.

## Status

Budget enforcement has landed. `hiveplane.budget` provides a per-model
`CostTable` (unknown models fail loudly), a `BudgetService` that prices usage
from tokens and enforces per-run, per-day, and per-team limits, a spend/
attribution store, and burn-metric hooks. The service implements the admission
`BudgetGate` seam and is wired into `RunService.record_usage`, which fails a run
when a limit is exceeded. Cost showback analytics (cost-per-completed-task,
waste detection, ROI flags) remain with the cost-service effort.

## M13 — Budget Enforcement

**Issues:** [#36](https://github.com/deghosal-2026/hiveplane/issues/36) · [#37](https://github.com/deghosal-2026/hiveplane/issues/37)

- [ ] [#36](https://github.com/deghosal-2026/hiveplane/issues/36) — Cost table and per-model pricing
- [ ] [#37](https://github.com/deghosal-2026/hiveplane/issues/37) — Per-run and per-day budget enforcement

**Done when:** known token counts map to expected cost, unknown models fail loudly, a seeded over-budget run is blocked or escalated, and budget-burn metrics are emitted.

## Dependencies

- Part 1 (usage models)
- Part 4 (admission + usage events)

## Exit Gate (M13)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Budget enforcement design](../../design/budget-enforcement-design.md)
- [Cost service design](../../design/cost-service-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
