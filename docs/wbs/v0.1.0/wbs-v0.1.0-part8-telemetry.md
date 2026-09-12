# WBS v0.1.0 — Part 8: Telemetry & Observability

**Milestones:** M14-M15

## Goal

Export correlated traces, metrics, logs, and audit events for the fleet.

## M14 — OTel Pipeline

- [ ] Instrument the API and adapters with OpenTelemetry
- [ ] OTel Collector wired to Tempo (traces) and Prometheus (metrics)
- [ ] Run/workload/team correlation labels

## M15 — Fleet Metrics & Debug Context

- [ ] Core metrics: runs by state, budget burn, failures, escalations, intervention latency
- [ ] Tool-call and policy-decision metrics
- [ ] Trace-linked debug context for a run

## Exit Criteria

- [ ] A run's full execution story is visible from its trace
- [ ] Fleet metrics are queryable in Prometheus/Grafana
- [ ] Exit gate checklist passed

## See Also

- [Telemetry design](../../design/telemetry-design.md)
- [Observability](../../observability.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
