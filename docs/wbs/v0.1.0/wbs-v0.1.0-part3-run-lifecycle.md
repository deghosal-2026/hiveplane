# WBS v0.1.0 — Part 3: Run Lifecycle & Execution API

**Milestones:** M5-M7

## Goal

Submit tasks, persist the run state machine, and expose intervention actions.

## M5 — Task Submission

- [ ] `POST /runs` (agent, task payload, caller identity)
- [ ] Run identity and admission checks (registry + policy + budget)
- [ ] Queue assignment

## M6 — Run State Machine

- [ ] Implement states and transitions (see [D2](../../design/run-lifecycle-design.md))
- [ ] Persist transitions with attribution before side effects
- [ ] `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`

## M7 — Intervention API

- [ ] `POST /runs/{id}/pause`, `/resume`, `/stop`
- [ ] Attribution and audit for every operator action
- [ ] Resume preserves run context

## Exit Criteria

- [ ] A run moves queued → running → completed with a complete event log
- [ ] Pause/resume/stop work and are audited
- [ ] Exit gate checklist passed

## See Also

- [Run lifecycle design](../../design/run-lifecycle-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
