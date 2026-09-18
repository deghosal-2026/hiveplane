# HivePlane Observability

HivePlane is OpenTelemetry-native. Every run emits traces, metrics, logs, and audit events, and the control plane is the place they converge so a fleet can be inspected without jumping between dashboards.

## Signals

| Signal | Purpose |
|--------|---------|
| **Traces** | Follow a run across planning, tool calls, model calls, retries, approvals, and cost events |
| **Metrics** | Budget burn, run counts, failure rates, latency by agent and team; certification pass rates, drift detections, cost showback |
| **Logs** | Structured, run-correlated logs |
| **Audit events** | Operator actions, policy outcomes, certification decisions, model-swap blocks — tamper-evident |
| **Agent health** | Readiness, failure rate, SLO status, drift indicators — first-class fleet signals |

## Trace-Linked Debug Context

When an agent misbehaves, an operator should be able to open the run and see the execution story — not a flat log. Trace-linked debug context is a first-class output:

- planning spans (what the agent decided and why)
- tool call spans (inputs, outputs, policy decisions, shaping results)
- model call spans (model identity, token usage, latency)
- retry spans (what failed and what was retried)
- approval spans (who approved, when, with what evidence)
- cost spans (spend per call, cumulative burn vs. budget)
- certification spans (benchmark task results, pass/fail, attestation reference)

## Agent Health Model

Agent health is a first-class fleet signal, not a derived dashboard. Each workload's health is derived from its `spec.health.slo` config and run history.

| Signal | How it's derived | Purpose |
|--------|------------------|---------|
| **Readiness** | Recent success rate ≥ `target_success_rate` over the evaluation window | Is this agent safe to run right now? |
| **Failure rate** | Failed runs / total runs over the evaluation window | Is the agent degrading? |
| **SLO status** | `meeting` or `breaching` based on `target_success_rate` and `target_latency_p99_seconds` | Is the agent meeting its contract? |
| **Drift** | Re-certification result vs. baseline; drift indicator flag if performance decayed beyond `grace_margin` | Has the agent drifted from its certified baseline? |

Health signals are surfaced in the fleet dashboard, the run detail page, and the weekly fleet review. A drifting agent is auto-quarantined by the drift detector before customer impact.

## Certification Metrics

Certification is the primary signal. These metrics prove the pipeline works and is trustworthy.

| Metric | Why it matters | Target (v0.1.0) |
|--------|----------------|-----------------|
| Agents certified for production | Proves the certification pipeline works end-to-end | ≥ 1 |
| Certification pass rate on first attempt | Measures benchmark quality and agent readiness | measured, no target |
| Time from registration to certification | The onboarding friction | < 10 minutes |
| Regressions caught by re-certification | Proves the promotion gate works | ≥ 1 seeded |
| Drift detections (auto-quarantine) | Proves drift detection works | ≥ 1 seeded |
| False quarantine rate | Drift detector isn't over-triggering | 0 in field test |
| Attestation verification on every production admission | Integrity guarantee | 100% |
| Model-swap blocks | Model binding works | ≥ 1 seeded |

### Attestation Verification

Every production admission triggers an attestation verification:

1. Load the latest signed attestation for the workload
2. Verify the signature (key-paired; unsigned or tampered attestations are rejected — threat T9)
3. Check the attestation is not expired
4. Verify `model.identity` in the attestation matches the runtime model (threat T11)
5. Log the verification result in the audit trail

If any step fails, the run is blocked and the owning team is notified.

## Cost Showback Metrics

Cost is enforced in real time (budgets) and attributed after the fact (showback). These metrics surface in the fleet review and spend views.

| Metric | Why it matters | How it's computed |
|--------|----------------|-------------------|
| Spend by team | Attribute cost to owning teams | Sum of run costs grouped by `metadata.owner` |
| Spend by agent | Attribute cost to individual workloads | Sum of run costs grouped by `metadata.name` |
| Cost-per-completed-task | Real cost including retries, failed loops, escalations | Total spend / completed (not attempted) runs |
| Waste detection | Identify spend on failed or low-value runs | Spend on failed runs + spend on runs that exceeded budget without completing |
| ROI flags | Flag expensive-but-low-value agents | Spend vs. observed outcome/value; surfaced in weekly fleet review |
| Budget burn rate | Track spend velocity | Cumulative spend / budget over time; projected exhaustion |

Cost showback is not an after-the-fact dashboard — it's computed from real-time usage events and surfaced alongside health and certification status in the fleet review.

## Key Metrics Summary

| Category | Metric | Source |
|----------|--------|--------|
| Fleet | Runs by state | State store |
| Fleet | Budget burn (total, per team, per agent) | Budget & Cost Service |
| Fleet | Failures | Run lifecycle |
| Fleet | Escalations | Policy engine |
| Fleet | Intervention latency (median) | Audit events |
| Certification | Agents certified | Certification engine |
| Certification | Pass rate | Benchmark runner |
| Certification | Drift detections | Drift detector |
| Certification | Attestation verifications | Certification engine |
| Certification | Model-swap blocks | Policy engine / certification |
| Cost | Spend by team / agent | Budget & Cost Service |
| Cost | Cost-per-completed-task | Budget & Cost Service |
| Cost | Waste | Budget & Cost Service |
| Cost | ROI flags | Budget & Cost Service |
| Health | Readiness | Agent health model |
| Health | Failure rate | Agent health model |
| Health | SLO status | Agent health model |
| Health | Drift indicators | Drift detector |

## OpenTelemetry

The reference stack uses the OpenTelemetry Collector, with Tempo for traces and Prometheus/Grafana for metrics. Logs are structured JSON, correlated by `run_id` and `workload_name`. Audit events are written to a tamper-evident append-only log in PostgreSQL.

The control plane is instrumented with OpenTelemetry (M19). Every HTTP request
opens a server span, and the run path emits `admission`, `execution`, `sandbox`,
`tool_call`, `policy_decision`, `model_call`, `approval`, `fan_out`, and
`certification` spans. Each carries `run_id`, `workload`, and `team`, so a run's
execution story is one trace from the API call to adapter execution. Adapters run
on background threads; span context is propagated across the thread hop.

### Local stack

`docker compose up` starts the collector, Tempo, Prometheus, and Grafana with
healthchecks and provisioned datasources and dashboards:

| Surface | URL |
|---------|-----|
| Grafana (HivePlane Overview dashboard) | http://localhost:3000 |
| Tempo (trace search) | http://localhost:3200 |
| Prometheus | http://localhost:9090 |
| Collector health | http://localhost:13133 |

Point the API at the collector with `HIVEPLANE_OTEL__ENDPOINT=http://localhost:4318`
(Compose sets this automatically).

### Metrics (M20)

The control plane exports fleet, cost, and certification metrics over OTLP
(`hiveplane_runs_total`, `hiveplane_run_duration_seconds`,
`hiveplane_failures_total`, `hiveplane_escalations_total`,
`hiveplane_intervention_latency_seconds`, `hiveplane_budget_burn_usd`,
`hiveplane_spend_usd_total`, `hiveplane_budget_exceeded_total`,
`hiveplane_tool_calls_total`, `hiveplane_policy_decisions_total`,
`hiveplane_certifications_total`, `hiveplane_certification_duration_seconds`,
`hiveplane_regressions_caught_total`, `hiveplane_attestation_verifications_total`,
`hiveplane_model_swap_blocks_total`). The HivePlane Overview dashboard graphs
them; see the [telemetry design](design/telemetry-design.md#metrics) for the full
list. Drift metrics are deferred to v0.2.0 with the drift detector.

### Run execution story (M20)

`GET /runs/{run_id}/story` returns a run's execution story — admission, state
transitions, policy decisions, tool calls, model calls, sandbox events,
deliveries, and approvals — with the run's `trace_id` linking to the full OTel
trace. This backs the operator UI's run-detail page.

## Observability Contract

Each registered workload declares an observability contract (`spec.observability.contract`): which signals it emits, spans it produces, and metrics it exposes. This makes fleet-level comparisons possible. The `standard` contract requires:

- run-level trace with planning, tool-call, model-call, and cost spans
- budget burn metric per run
- state transition events
- audit events for every policy decision and operator action

## See Also

- [Telemetry design](design/telemetry-design.md)
- [PRD 02: Architecture](prd/02-architecture.md)
- [PRD 05: Features](prd/05-features.md)
- [PRD 07: Success Metrics](prd/07-success-metrics.md)
- [PRD 06: Security Baseline](prd/06-security-baseline.md)
