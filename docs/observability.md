# HivePlane Observability

HivePlane is OpenTelemetry-native. Every run emits traces, metrics, logs, and audit events, and the control plane is the place they converge so a fleet can be inspected without jumping between dashboards.

## Signals

| Signal | Purpose |
|--------|---------|
| **Traces** | Follow a run across planning, tool calls, model calls, and retries |
| **Metrics** | Budget burn, run counts, failure rates, latency by agent and team |
| **Logs** | Structured, run-correlated logs |
| **Audit events** | Operator actions and policy outcomes, tamper-evident |

## Trace-Linked Debug Context

When an agent misbehaves, an operator should be able to open the run and see the execution story — not a flat log. Trace-linked debug context is a first-class output (`docs/README.md` → Core Workflows, Workflow 2).

## OpenTelemetry

The reference stack uses the OpenTelemetry Collector, with Tempo for traces and Prometheus/Grafana for metrics.

## Observability Contract

Each registered workload declares an observability contract: which signals it emits, spans it produces, and metrics it exposes. This makes fleet-level comparisons possible.

To be completed in the v0.1.0 WBS (Part 8).

## See Also

- [Telemetry design](design/telemetry-design.md)
- [Success metrics](design/prd/07-success-metrics.md)
