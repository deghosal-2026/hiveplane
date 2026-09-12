# HivePlane v0.1.0 — Field Test Plan

> Status: plan — to be executed in M18.

## Objective

Prove the control loop with three real agent workloads running through one lifecycle, and demonstrate budget enforcement, approvals, and intervention.

## Test Workloads

| Workload | Runtime | What it does | Stresses |
|----------|---------|--------------|----------|
| `repo-agent` | raw-worker | Summarizes open PRs via tools | read-only tool policy, usage reporting |
| `docs-agent` | langgraph | Drafts docs updates | LangGraph adapter, state transitions |
| `incident-agent` | raw-worker | Triages an alert | destructive tool approval, budget escalation |

## Phases

### Phase 1 — Baseline Validation

- [ ] Docker Compose stack starts with one command
- [ ] All three workloads register successfully
- [ ] Registry, state store, and telemetry are reachable

### Phase 2 — Lifecycle

- [ ] Submit a task to each workload
- [ ] Verify queued → running → completed transitions and event logs
- [ ] Verify usage is reported and priced

### Phase 3 — Governance

- [ ] Seed an over-budget run; verify it is blocked/escalated
- [ ] Trigger a destructive tool call on `incident-agent`; verify approval is required
- [ ] Approve and deny paths both resume/fail correctly

### Phase 4 — Intervention & Durability

- [ ] Pause a running run; verify it stops advancing
- [ ] Restart the control plane; verify the paused run resumes with context intact
- [ ] Stop a run; verify cancellation is audited

### Phase 5 — Review

- [ ] Fleet view shows health, failures, spend by worker and team
- [ ] Trace-linked debug context renders the execution story
- [ ] Audit trail complete for all runs

## Acceptance Criteria

| # | Criterion | Target |
|---|-----------|--------|
| A1 | Agents through full lifecycle | 3/3 |
| A2 | Budget overrun caught before manual discovery | ≥ 1 |
| A3 | Guarded tool call requires approval | pass |
| A4 | Paused run survives restart | pass |
| A5 | Audit completeness | 100% |
| A6 | Median time to inspect + stop a bad run | < 2 min |

## LLM Configuration

Both local (OMLX-style OpenAI-compatible endpoint) and cloud providers should be exercised for at least one workload each.

## Reporting

Results, raw metrics, and learnings are recorded in [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md).
