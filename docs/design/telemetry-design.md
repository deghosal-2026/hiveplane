# D8: Telemetry Design

> Status: draft.

## Problem

A fleet is only operable if its behavior is observable without switching tools. HivePlane is OpenTelemetry-native and correlates traces, metrics, logs, and audit events by run (DD-06).

## Signals

| Signal | Backend | Correlation |
|--------|---------|-------------|
| Traces | OTel Collector → Tempo | `run_id`, `workload`, `team` |
| Metrics | Prometheus | labels: workload, team, state |
| Logs | structured logs | `run_id` |
| Audit | PostgreSQL audit log | actor, run_id, policy rule |

## Key Metrics

- runs by state and by workload
- budget burn (per run, per workload, per team)
- failures and escalations by workload
- intervention latency (time to inspect/stop a bad run)
- tool-call counts and policy decisions by outcome

## Trace-Linked Debug Context

Opening a run should render the execution story: planning, tool calls, model calls, retries, approvals, and cost accumulation — derived from the same trace.

## Open Questions

- sampling strategy for high-volume fleets
- how much trace data is durably retained vs sampled
