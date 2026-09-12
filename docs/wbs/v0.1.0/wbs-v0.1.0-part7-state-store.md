# WBS v0.1.0 — Part 7: State Store & Persistence

**Milestone:** M13

## Goal

Make desired state, run state, and audit history durable and queryable.

## M13 — State Store

- [ ] PostgreSQL schema: workloads, versions, runs, run_events, usage_events, audit_log, approvals
- [ ] Migrations
- [ ] Run survives control-plane restart
- [ ] Append-only guarantees and tamper-evident audit records
- [ ] Query paths by run, workload, owner/team, and time window

## Exit Criteria

- [ ] A paused run resumes correctly after a restart
- [ ] Audit and event logs are complete for every run
- [ ] Exit gate checklist passed

## See Also

- [State store design](../../design/state-store-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
