# WBS v0.1.0 — Part 6: Budget Enforcement

**Milestone:** M13 · **Issues:** #36-#37

## Goal

Stop spend during execution, not after. Price usage accurately and enforce per-run and per-day limits.

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
