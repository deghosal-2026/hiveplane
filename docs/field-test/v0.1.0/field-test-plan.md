# HivePlane v0.1.0 — Field Test Plan

> Status: plan — to be executed in M18.

## Objective

Prove the certified control loop with three real agent workloads. An agent is registered, certified against a benchmark, and only then allowed to run in production. The field test exercises certification, sandbox execution, tool-output shaping, budget enforcement, intervention, durability, and fleet review — all the v0.1.0 release gates.

## Test Workloads

| Workload | Runtime | What it does | Stresses |
|----------|---------|--------------|----------|
| `repo-agent` | raw-worker | Summarizes open PRs via tools | read-only tool policy, usage reporting, certification at production threshold |
| `docs-agent` | langgraph | Drafts docs updates | LangGraph adapter, state transitions, output shaping on large payloads |
| `incident-agent` | raw-worker | Triages an alert | destructive tool in sandbox, approval flow, budget escalation, fan-out delivery |

Each workload has a benchmark corpus under `examples/corpora/<name>/v1/` with ≥ 5 tasks and deterministic pass/fail checks.

## Phases

### Phase 1 — Baseline Validation

- [ ] Docker Compose stack starts with one command (`docker compose up -d`)
- [ ] `hiveplane init` scaffolds a working project in < 5 minutes
- [ ] Registry, state store, telemetry pipeline, and certification engine are reachable
- [ ] All three workloads register successfully (`hiveplane register`)
- [ ] `--dry-run` registration reports what would be enforced without admitting runs
- [ ] MCP Tool Registry is seeded with the tools referenced by each workload

### Phase 2 — Certification

- [ ] Run `hiveplane certify repo-agent` — benchmark executes in isolated environment
- [ ] Run `hiveplane certify docs-agent` — benchmark executes in isolated environment
- [ ] Run `hiveplane certify incident-agent` — benchmark executes in isolated environment
- [ ] Verify all three pass at `production_threshold` and status becomes `certified`
- [ ] Verify each certification produces a **signed attestation** (benchmark version, model, eval results, timestamp, environment, signer)
- [ ] Verify `hiveplane certs show <id>` displays the full attestation
- [ ] Verify `hiveplane certs list` shows all three with `certified` status
- [ ] Verify an **uncertified** workload (register a fourth without certifying) is **refused admission** to a production context
- [ ] Verify a **model swap** (change `model.identity` without re-certification) is **blocked** at run start

### Phase 3 — Lifecycle

- [ ] Submit a task to each certified workload via `hiveplane submit`
- [ ] Verify `queued → running → completed` state transitions and event logs
- [ ] Verify usage is reported and priced (token usage, cost per run)
- [ ] Verify run state is persisted and queryable via `hiveplane runs list` and `hiveplane runs show <id>`
- [ ] Verify trace-linked debug context renders the execution story (planning, tool calls, model calls, cost)

### Phase 4 — Governance

- [ ] **Budget**: Seed an over-budget run on `incident-agent`; verify it is **blocked** before expensive work
- [ ] **Budget**: Verify daily aggregate budget enforcement refuses new runs when daily ceiling is hit
- [ ] **Sandbox**: Trigger a destructive tool call on `incident-agent` (e.g. `pagerduty.acknowledge`); verify it executes in the **isolated sandbox context**
- [ ] **Sandbox**: Verify resource caps are enforced (seed a run that exceeds `wall_clock_seconds` → killed)
- [ ] **Sandbox**: Verify egress is restricted (seed a call to a non-allowlisted host → blocked)
- [ ] **Approval**: Verify the destructive tool call **requires approval** before execution
- [ ] **Approval**: Test approve path → run resumes and completes
- [ ] **Approval**: Test deny path → run fails with attributed denial
- [ ] **Output shaping**: Seed `docs-agent` with a tool that returns a large payload; verify it is **truncated** per `max_bytes_per_tool_call`
- [ ] **Output shaping**: Verify filter rules are applied (secrets redacted, patterns matched)
- [ ] **Output shaping**: Verify cumulative output budget is enforced (total output exceeding `max_output_bytes` → run paused)

### Phase 5 — Intervention & Durability

- [ ] Pause a running run via `hiveplane runs pause <id>`; verify it stops advancing
- [ ] Restart the control plane (`docker compose restart`); verify the paused run **resumes** with context intact
- [ ] Resume the paused run; verify it continues to completion
- [ ] Stop a run via `hiveplane runs stop <id>`; verify cancellation is **audited**
- [ ] **Fan-out**: Verify completed runs deliver results to configured `fan_out` destinations (Slack/webhook)
- [ ] **Fan-out**: Verify failed runs deliver to `on_failure` destinations (e.g. Jira)
- [ ] **Fan-out**: Verify escalated runs deliver to `on_escalation` destinations
- [ ] Verify fan-out messages include a **trace link** and **attestation link**

### Phase 6 — Review

- [ ] Fleet view shows all three workloads with health, failures, and spend by worker and team
- [ ] **Certification dashboard** shows certification status, pass rates, last-certified timestamps
- [ ] **Spend view** shows cost showback by team and agent, including cost-per-completed-task
- [ ] **Agent health** shows readiness, failure rate, and SLO status per workload
- [ ] Trace-linked debug context renders the full execution story for any run
- [ ] Audit trail is **complete** for every run in the field test (100%)

## Acceptance Criteria

All v0.1.0 release gates from [PRD 07](../../prd/07-success-metrics.md):

| # | Criterion | Target | Phase |
|---|-----------|--------|-------|
| A1 | Three real agents registered | 3/3 | Phase 1 |
| A2 | At least one agent certified for production via benchmark | ≥ 1 | Phase 2 |
| A3 | An uncertified agent is refused admission to a production context | pass | Phase 2 |
| A4 | A seeded manifest change is blocked by re-certification (regression caught) | ≥ 1 seeded | Phase 2 |
| A5 | Attestation is signed and verified on read | pass | Phase 2 |
| A6 | Model-swap blocks (certified on A, running on B) | ≥ 1 seeded | Phase 2 |
| A7 | Agents through full lifecycle (submit → queued → running → completed) | 3/3 | Phase 3 |
| A8 | Budget enforcement demonstrably blocks an over-budget run | ≥ 1 | Phase 4 |
| A9 | Execution isolation demonstrably caps a destructive run | pass | Phase 4 |
| A10 | Tool-output shaping demonstrably truncates a large payload | ≥ 1 shaped run | Phase 4 |
| A11 | Guarded tool call requires approval (approve + deny paths) | pass | Phase 4 |
| A12 | Paused run survives control-plane restart | pass | Phase 5 |
| A13 | Operators can inspect and stop any run from one surface | pass | Phase 5 |
| A14 | Result fan-out delivered to configured destinations | ≥ 1 fanned-out result | Phase 5 |
| A15 | Audit trail complete for every run in the field test | 100% | Phase 6 |
| A16 | Median time to inspect and stop a bad run | < 2 minutes | Phase 6 |
| A17 | Docker Compose stack starts with one command | pass | Phase 1 |
| A18 | `hiveplane init` scaffolds a working project in < 5 minutes | pass | Phase 1 |
| A19 | Certification dashboard renders fleet cert status | pass | Phase 6 |
| A20 | Spend view shows cost showback by team and agent | pass | Phase 6 |

## LLM Configuration

Both local (OMLX-style OpenAI-compatible endpoint) and cloud providers should be exercised for at least one workload each. The `model.identity` in each manifest must match the provider actually used — the model-swap defense will block mismatches.

## Reporting

Results, raw metrics, and learnings are recorded in [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md).
