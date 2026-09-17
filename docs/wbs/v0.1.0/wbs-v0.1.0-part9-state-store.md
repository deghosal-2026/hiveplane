# WBS v0.1.0 — Part 9: State Store & Persistence

**Milestones:** M18 · **Issues:** #46-#47

> **Status:** M18 complete — #46, #47 implemented (2026-09-16). DB-gated tests run in CI
> (postgres:16 service); they skip locally when no database is reachable.

## Goal

Make desired state, run state, certifications, attestations, tools, triggers, and audit history durable and queryable.

## M18 — State Store

**Issues:** [#46](https://github.com/deghosal-2026/hiveplane/issues/46) · [#47](https://github.com/deghosal-2026/hiveplane/issues/47)

- [x] [#46](https://github.com/deghosal-2026/hiveplane/issues/46) — PostgreSQL schema and migrations
- [x] [#47](https://github.com/deghosal-2026/hiveplane/issues/47) — Durability, append-only audit, and retention

**Done when:** migrations apply and roll back cleanly; tables exist for workloads, versions, runs, run_events, usage_events, audit_log, approvals, certifications, attestations, tools, trigger_rules, drift_schedules, fan_out_deliveries, health_signals, cost_attributions; kill-and-restart preserves a paused run; audit records detect tampering.

## Dependencies

- Part 1 (models)
- Part 3 (certifications + attestations)

## Exit Gate (M18)

- [x] All tests in the system pass: `pytest` — 550 passed, 8 skipped locally (DB-gated); CI runs them
- [x] Code coverage total > 95% — 96%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] Update all relevant docs affected by this milestone — state-store design, USER_GUIDE, index
- [x] Verify all issues in this milestone are done
- [x] Close all completed issues — #46, #47
- [x] Commit and push changes

## See Also

- [State store design](../../design/state-store-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
