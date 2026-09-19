# D9: Operator UI Design

> Status: draft

## Problem

Operators need one surface to see the fleet, inspect a run, approve an escalation, understand spend, manage certifications, monitor agent health, configure triggers, and manage tools — and to act, not just look. After the PRD rewrite, the UI must add: a certification dashboard (fleet view of cert status, pass rates, trends, last-certified timestamps, quarantine history), an agent health view, a triggers management UI, and a tools/MCP registry UI.

See [PRD 02: Architecture](../prd/02-architecture.md) § Operator UI and [PRD 05: Features](../prd/05-features.md) § Operator Surface.

## v0.1.0 Screens

| Screen | Contents |
|--------|----------|
| Fleet list | All workloads: owner, certification status, state counts, recent failures, budget burn, health status |
| Run detail | State timeline, tool calls, model calls, cost, trace link, output shaping events, sandbox status, fan-out delivery status |
| Approval queue | Pending escalations with evidence (trace link, blast-radius score, tool output) and approve/deny actions |
| **Certification dashboard** | Fleet view of certification status, pass rates, trends, last-certified timestamps, quarantine history |
| Spend view | Budget burn by workload and team, cost-per-completed-task, waste breakdown, ROI flags |

## v0.2.0 Screens (additions)

| Screen | Contents |
|--------|----------|
| Agent health view | Per-workload readiness, failure rate, SLO status, drift indicator, health trend |
| Triggers management | List/create/edit/delete trigger rules per workload; trigger event history; dedup stats |
| Tools & MCP registry | List registered MCP tools, trust levels, add/edit tools; see which workloads reference each tool |

## Certification Dashboard

The certification dashboard is the fleet-level view of the certification pipeline — the thesis of HivePlane (PRD 05: certification pipeline, CUJ-9).

### Fleet Certification Overview

| Element | Contents |
|---------|----------|
| Certification status summary | Counts by status: `certified`, `provisional`, `uncertified`, `quarantined` |
| Pass-rate trend | Sparkline of pass rates over time per workload or fleet-wide |
| Last-certified timestamps | Per workload: when last certified, when expires, time until next re-cert |
| Quarantine history | List of past quarantine events with reason (drift, failed re-cert, expired) and resolution |
| Regression log | Recent regressions caught by re-certification with replay trace links |

### Per-Workload Certification Detail

| Element | Contents |
|---------|----------|
| Current status | `certified` / `provisional` / `uncertified` / `quarantined` with timestamp |
| Attestation | Attestation ID, signer, signature verification status, model identity |
| Benchmark results | Per-task pass/fail from last certification, pass rate, critical failures, latency |
| Certification history | Timeline of all certifications with regression diffs |
| Drift schedule | Re-cert interval, last re-cert, next re-cert, drift indicator |
| Actions | `hiveplane certify <workload>`, compare versions, view replay trace |

### Certification Trend View

| Element | Contents |
|---------|----------|
| Pass-rate over time | Line chart of pass rate per certification run |
| Regression heatmap | Which tasks regressed across certifications |
| Model-identity history | Which model was used for each certification |
| Drift events | Markers on the timeline when drift was detected and auto-quarantine triggered |

## Agent Health View

The agent health view provides fleet-level and per-workload health signals (PRD 05: observability & health, CUJ-9).

### Fleet Health Overview

| Element | Contents |
|---------|----------|
| Health status summary | Counts by status: `healthy`, `degraded`, `unhealthy`, `quarantined` |
| Readiness grid | Per workload: ready / not ready, last probe time |
| Failure rate leaderboard | Workloads ranked by recent failure rate |
| SLO status | Per workload: availability, quality, error budget remaining |
| Drift indicators | Per workload: drift indicator (stable / drifting) |

### Per-Workload Health Detail

| Element | Contents |
|---------|----------|
| Readiness probe history | Probe results over time |
| Failure rate trend | Rolling failure rate with threshold marker |
| SLO compliance | Availability and quality against targets, error budget burn-down |
| Drift history | Re-certification results over time, drift detection events |
| Recent runs | Last N runs with state, duration, cost, outcome |

## Triggers Management UI

The triggers management UI lets operators configure and monitor event-driven run auto-start (PRD 05: triggers, CUJ-3).

### Trigger Rule List

| Element | Contents |
|---------|----------|
| Per workload | List of trigger rules: type, match criteria, mode, max concurrent |
| Create trigger | Form to add a new trigger rule (type, match, mode, schedule for cron) |
| Edit/delete trigger | Inline edit and delete with confirmation |

### Trigger Event History

| Element | Contents |
|---------|----------|
| Recent events | Trigger events received: type, source, timestamp, matched workload |
| Dedup stats | Duplicate events filtered, dedup rate |
| Run outcomes | For each trigger-originated run: state, duration, result |

## Tools & MCP Registry UI

The tools & MCP registry UI lets operators manage the MCP tool registry (PRD 05: tools & MCP, CUJ-8).

### Tool List

| Element | Contents |
|---------|----------|
| Tool registry | All registered MCP tools: tool ID, name, MCP server, trust level, description |
| Filter | By trust level (`read_only` / `destructive`), by MCP server |
| Add tool | Form to register a new MCP tool (tool ID, endpoint, trust level, parameters schema) |
| Edit tool | Inline edit; shows which workloads reference the tool (and may need re-certification) |

### Per-Tool Detail

| Element | Contents |
|---------|----------|
| Tool definition | Full parameters schema, trust level, MCP server endpoint |
| Referencing workloads | List of workloads that reference this tool in their manifest |
| Recent calls | Recent tool calls with policy decision, outcome, and run link |
| Trust level change history | Audit trail of trust level changes |

## Spend View (expanded)

The spend view provides cost showback and ROI visibility (PRD 05: cost & ROI, CUJ-9).

| Element | Contents |
|---------|----------|
| Spend by team | Bar chart of team spend for the selected period |
| Spend by workload | Table: workload, total spend, completed tasks, cost-per-completed-task, waste, ROI flag |
| Waste breakdown | Pie chart of waste by category (failed runs, cancelled, escalated, idle, retry overhead) |
| ROI flags | Workloads flagged `expensive_low_value` with recommendation to review/retire |
| Budget utilization | Per workload: budget vs. actual, utilization percentage, projected end-of-period spend |
| Model spend | Spend broken down by model identity |

## Run Detail (expanded)

The run detail page shows the full execution story (PRD 05: observability & health).

| Element | Contents |
|---------|----------|
| State timeline | Visual timeline of state transitions with timestamps and attribution |
| Certification status at admission | Status, attestation ID, model-identity verification result |
| Tool calls | Each tool call: tool ID, trust level, policy decision, shaped output (before/after), injection scan result |
| Model calls | Each model call: model identity, token usage, cost |
| Sandbox status | Whether sandbox was used, resource caps, egress blocks |
| Output shaping events | Truncation, redaction, masking events with before/after sizes |
| Cost | Run total cost, broken down by model calls and tool calls |
| Fan-out delivery status | Per destination: type, status, delivered at, retry count |
| Trace link | Link to the full OTel trace |
| Attestation link | Link to the certification attestation (if applicable) |

## Principles

- action-first: pause/resume/stop and approve/deny are first-class
- every operator action is attributed and audited (DD-07)
- show *why* a policy decision was made (rule, blast-radius score, context)
- certification status is visible everywhere — fleet list, run detail, health view
- keep v0.1.0 to fleet list, run detail, approval queue, certification dashboard, and spend view before adding breadth
- triggers and tools management land in v0.2.0
- agent health view lands in v0.3.0 (health signals may be visible in v0.1.0 but not as a dedicated screen)

## Stack

> v0.1.0 ships a **server-rendered Python UI** (FastAPI + Jinja2) that is an HTTP
> client of the control plane, to stay inside the repository's pytest/ruff/mypy
> gates without a Node toolchain. A React SPA remains the target and can replace
> the templates behind the same API. See
> [M22 Operator UI implementation](operator-ui-implementation.md).

React, backed by the Execution API, Registry Service, and telemetry pipeline.

## Open Questions

- real-time updates (polling vs streaming / SSE)
- how to present trace-linked debug context compactly
- certification dashboard layout (single fleet view vs per-team tabs)
- whether trigger rule editing should require approval (since triggers affect when agents run)
- how to surface model-identity mismatch warnings prominently

## See Also

- [Workload manifest](workload-manifest-design.md) — fields displayed in fleet list and tool/trigger management
- [Run lifecycle](run-lifecycle-design.md) — run states and transitions displayed in run detail
- [Registry service](registry-service-design.md) — APIs backing certification dashboard, tools registry
- [Policy engine](policy-engine-design.md) — policy decision display in run detail and approval queue
- [Budget enforcement](budget-enforcement-design.md) — cost data backing spend view
- [Telemetry](telemetry-design.md) — metrics and health signals backing health view
- [Features](../prd/05-features.md) — § Operator Surface
- [Design decisions](design-decisions.md) — DD-07
