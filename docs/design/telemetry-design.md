# D8: Telemetry Design

> Status: draft

## Problem

A fleet is only operable if its behavior is observable without switching tools. HivePlane is OpenTelemetry-native and correlates traces, metrics, logs, and audit events by run (DD-06). After the PRD rewrite, telemetry must also include: agent health model (readiness, recent failure rate, SLO status, drift as first-class signals), certification metrics (pass rate, drift detections, attestation verification rate, model-swap blocks), and cost showback metrics (spend attribution, waste, ROI).

See [PRD 02: Architecture](../prd/02-architecture.md) § Telemetry Pipeline and [PRD 05: Features](../prd/05-features.md) § Observability & Health.

## Signals

| Signal | Backend | Correlation |
|--------|---------|-------------|
| Traces | OTel Collector → Tempo | `run_id`, `workload`, `team`, `certification_status` |
| Metrics | Prometheus | labels: workload, team, state, certification_status, trust_level |
| Logs | structured logs | `run_id`, `workload`, `team` |
| Audit | PostgreSQL audit log | actor, run_id, policy rule, certification_status |
| Health signals | PostgreSQL `health_signals` table | workload, last_updated |

## Key Metrics

### Core Run Metrics (existing)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `hiveplane_runs_total` | counter | workload, team, state | Runs by state and workload |
| `hiveplane_run_duration_seconds` | histogram | workload, team | Run duration distribution |
| `hiveplane_budget_burn_usd` | gauge | workload, team, run_id | Budget burn per run/workload/team |
| `hiveplane_failures_total` | counter | workload, team, reason | Failures by workload and reason |
| `hiveplane_escalations_total` | counter | workload, team, policy_rule | Escalations by workload and rule |
| `hiveplane_intervention_latency_seconds` | histogram | workload | Time to inspect/stop a bad run |
| `hiveplane_tool_calls_total` | counter | workload, tool_id, trust_level, outcome | Tool call counts and policy decisions |
| `hiveplane_policy_decisions_total` | counter | workload, decision, rule | Policy decisions by outcome |

### Agent Health Metrics (new)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `hiveplane_agent_readiness` | gauge | workload | Readiness status (1=ready, 0=not ready) |
| `hiveplane_agent_failure_rate` | gauge | workload | Recent failure rate (rolling window) |
| `hiveplane_agent_slo_availability` | gauge | workload | Availability SLO status (0–1) |
| `hiveplane_agent_slo_quality` | gauge | workload | Quality SLO status (0–1) |
| `hiveplane_agent_error_budget_remaining` | gauge | workload | Remaining error budget (0–1) |
| `hiveplane_agent_drift_indicator` | gauge | workload | Drift indicator (1=drifting, 0=stable) |
| `hiveplane_agent_health_status` | gauge | workload | Health status (0=quarantined, 1=unhealthy, 2=degraded, 3=healthy) |

### Certification Metrics (new)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `hiveplane_certifications_total` | counter | workload, status | Certification runs by outcome status |
| `hiveplane_certification_pass_rate` | gauge | workload, threshold_type | Pass rate (staging vs production) |
| `hiveplane_certification_duration_seconds` | histogram | workload | Time to complete a certification run |
| `hiveplane_drift_detections_total` | counter | workload | Drift detections (auto-quarantine events) |
| `hiveplane_attestation_verifications_total` | counter | workload, result | Attestation verification on read (verified/failed) |
| `hiveplane_model_swap_blocks_total` | counter | workload | Model-swap blocks (runtime model ≠ attestation) |
| `hiveplane_regressions_caught_total` | counter | workload | Regressions caught by re-certification |
| `hiveplane_false_quarantines_total` | counter | workload | False quarantine events (drift detector over-trigger) |
| `hiveplane_certified_agents` | gauge | team | Count of certified agents per team |

### Cost Showback Metrics (new)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `hiveplane_spend_usd_total` | counter | team, workload, model | Total spend attributed |
| `hiveplane_cost_per_completed_task_usd` | gauge | workload, team | Cost per completed task |
| `hiveplane_waste_usd_total` | counter | team, workload, waste_category | Waste spend by category |
| `hiveplane_roi_flag` | gauge | workload | ROI flag (0=expensive_low_value, 1=balanced, 2=high_value) |
| `hiveplane_budget_exceeded_total` | counter | workload, team, level | Budget exhaustion events by level (run/day/team) |

### Trigger Metrics (new)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `hiveplane_trigger_events_total` | counter | workload, trigger_type | Trigger events received |
| `hiveplane_trigger_runs_started_total` | counter | workload, trigger_type | Runs auto-started by triggers |
| `hiveplane_trigger_dedup_total` | counter | workload, trigger_type | Duplicate trigger events deduped |

### Fan-Out Metrics (new)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `hiveplane_fan_out_deliveries_total` | counter | workload, destination_type, status | Fan-out delivery attempts |
| `hiveplane_fan_out_delivery_latency_seconds` | histogram | destination_type | Fan-out delivery latency |

### Sandbox Metrics (new)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `hiveplane_sandbox_runs_total` | counter | workload | Runs executed in sandbox |
| `hiveplane_sandbox_resource_usage` | gauge | workload, resource | Sandbox resource usage (memory, CPU) |
| `hiveplane_sandbox_egress_blocks_total` | counter | workload | Egress blocks in sandbox |

### Output Shaping Metrics (new)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `hiveplane_output_shaped_total` | counter | workload, shaping_action | Outputs shaped (truncated/redacted/masked) |
| `hiveplane_injection_blocks_total` | counter | workload, pattern_category | Injection patterns blocked |

## Agent Health Model

Agent health is a first-class fleet signal (PRD 05: observability & health). Each workload has a health record updated on every terminal transition and on readiness probe results:

| Signal | Source | Update Trigger |
|--------|--------|----------------|
| Readiness | Readiness probe (dry-run) | Probe execution (periodic, per `spec.health.readiness_probe.interval`) |
| Recent failure rate | Run terminal states | Every run terminal transition (rolling window) |
| SLO availability | `completed / (completed + failed)` | Every run terminal transition |
| SLO quality | `completed_without_escalation / total` | Every run terminal transition |
| Error budget | `1 - (actual_failure_rate / target_failure_rate)` | Every run terminal transition |
| Drift indicator | Drift detector re-certification | Periodic re-certification or failure spike trigger |

### Health Status Levels

| Status | Condition | Fleet Effect |
|--------|-----------|--------------|
| `healthy` | Failure rate < threshold, SLOs met, no drift | Normal operation |
| `degraded` | Failure rate elevated or SLO error budget low | Monitor; may tighten budgets |
| `unhealthy` | Failure rate > threshold or SLO violated | Review; consider re-certification |
| `quarantined` | Drift detected or certification expired | Runs blocked; team notified |

### Drift as a First-Class Signal

Drift is not just a certification event — it is a health signal. When the drift detector runs a re-certification and performance has decayed:

1. The workload's `certification.status` is set to `quarantined`.
2. The health signal's `drift_indicator` is set to 1.
3. The health status is set to `quarantined`.
4. The owning team is notified via fan-out.
5. Production runs are blocked until re-certification passes.

Drift is also triggered by failure spikes — if a workload's recent failure rate exceeds a threshold, an immediate re-certification is triggered (T12).

## Trace-Linked Debug Context

Opening a run should render the execution story: planning, tool calls, model calls, retries, approvals, cost accumulation, output shaping events, and certification status — derived from the same trace.

### Trace Spans

| Span | Contents |
|------|----------|
| `admission` | Certification check, model-identity check, budget check, policy pre-eval |
| `execution` | Run execution including sandbox provisioning |
| `tool_call` | Tool call + policy decision + output shaping + injection scan |
| `model_call` | Model call + token usage + cost |
| `policy_decision` | Decision, rule, blast-radius score |
| `approval` | Escalation + human review + resolution |
| `fan_out` | Delivery attempts per destination |
| `certification` | Benchmark run + eval + attestation signing (for certification runs) |

## Open Questions

- sampling strategy for high-volume fleets
- how much trace data is durably retained vs sampled
- whether health signals should be computed in the telemetry pipeline or in a dedicated health service
- whether drift indicator should be a binary signal or a continuous decay score
- alerting rules and notification channels for health status changes

## See Also

- [Run lifecycle](run-lifecycle-design.md) — run states that drive telemetry
- [Registry service](registry-service-design.md) — certification records, attestation verification
- [Budget enforcement](budget-enforcement-design.md) — cost attribution data
- [State store](state-store-design.md) — health_signals entity, usage_events
- [Operator UI](operator-ui-design.md) — health view, certification dashboard
- [Success metrics](../prd/07-success-metrics.md) — certification and product metric targets
- [Design decisions](design-decisions.md) — DD-06, DD-12
