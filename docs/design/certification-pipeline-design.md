# D10: Certification Pipeline Design

> Status: draft

## Problem

Production agent fleets ship changes with zero benchmark evidence. A prompt tweak, a model swap, or a tool upgrade reaches production silently — and the first signal of regression is a customer incident. SWE-bench proved that coding agents improve when they have a standardized, reproducible benchmark with clear pass/fail. HivePlane applies the same principle to production agent fleets: **agents earn the right to operate in production by passing a reproducible benchmark, and they are re-certified when they drift** (DD-09, DD-10, DD-11, DD-12).

No competitor gates production on evidence. This is the thesis component of HivePlane.

## Overview

The certification pipeline is four cooperating services plus an API surface:

```
 Register / Manifest Change
        │
        ▼
 ┌──────────────┐     ┌────────────────────┐     ┌──────────────────┐
 │  Benchmark   │────▶│  Certification     │────▶│  Promotion Gate  │
 │  Runner      │     │  Engine            │     │  (block / allow) │
 └──────────────┘     └────────────────────┘     └──────────────────┘
        │                     │                          │
        │                attest / quarantine             │
        │                     │                          │
        ▼                     ▼                          ▼
  replay trace        signed attestation         regression diff
                      (immutable)                (new vs previous)

                     ┌────────────────────┐
                     │  Drift Detector    │
                     │  (periodic re-cert) │
                     └────────────────────┘
                              │
                       decay below threshold
                              │
                              ▼
                        auto-quarantine
```

## Benchmark Runner

### Purpose

Execute a workload against its benchmark corpus in a controlled, reproducible environment. Each task in the corpus has a known expected outcome and a deterministic pass/fail check. Benchmark runs are isolated from production — they execute in the sandbox (D11) with a fixed model, fixed inputs, and no network side effects unless explicitly allowed by the corpus spec.

### Benchmark Corpus

Each workload type references a benchmark corpus. A corpus is a versioned set of tasks:

```yaml
corpus:
  id: incident-triage-corpus
  version: 3
  tasks:
    - id: task-001
      name: "classify severity from alert payload"
      input:
        alert_payload: !include fixtures/alert-001.json
      expected:
        outcome: "severity: high"
        required_fields: ["severity", "summary", "owner"]
      check:
        type: exact_match
        field: severity
        value: high
      critical: false
      timeout_seconds: 30
    - id: task-002
      name: "restart unhealthy service without touching siblings"
      input:
        alert_payload: !include fixtures/alert-002.json
        sandbox_cluster: mock-cluster-01
      expected:
        outcome: "restarted: service-b"
        forbidden_actions: ["restart service-a", "restart service-c"]
      check:
        type: action_audit
        required_actions: ["restart service-b"]
        forbidden_actions: ["restart service-a", "restart service-c"]
      critical: true
      timeout_seconds: 60
      allow_network: false
    - id: task-003
      name: "draft postmortem with RCA"
      input:
        incident_id: INC-0042
        timeline: !include fixtures/timeline-0042.json
      expected:
        outcome: "postmortem drafted"
      check:
        type: rubric
        criteria:
          - "identifies root cause"
          - "lists contributing factors"
          - "proposes remediation"
        min_score: 2
      critical: false
```

**Check types:**

| Type | Description | Determinism |
|------|-------------|-------------|
| `exact_match` | Output field matches expected value exactly | Fully deterministic |
| `action_audit` | Required actions present, forbidden actions absent in tool-call audit | Fully deterministic |
| `schema_match` | Output conforms to a JSON schema | Fully deterministic |
| `rubric` | Structured rubric scored by a secondary evaluator model | Deterministic given fixed evaluator model + temperature 0 |
| `custom` | A Python callable that receives the run trace and returns pass/fail | Deterministic given fixed inputs |

### Runner Execution Model

1. **Provision sandbox** — create an isolated execution context (D11) with the corpus-specified resource caps, network egress allowlist, and no shared filesystem with the control plane.
2. **Pin environment** — fix the model (by ID, not alias), fix the inputs (from corpus fixtures), disable any source of nondeterminism (temperature = 0 where applicable, no real-time clock dependencies, no external API calls unless `allow_network: true`).
3. **Execute each task** — submit the task through the normal execution path (adapter → sandbox → policy). The benchmark runner does not bypass policy; it runs under a benchmark-specific policy pack that mirrors production constraints.
4. **Collect results** — for each task, record: pass/fail, latency, token usage, tool calls, trace ID, and any policy decisions.
5. **Emit benchmark result** — a structured object with per-task results and aggregate metrics.

### Determinism Guarantees

| Property | Guarantee |
|----------|-----------|
| Model | Pinned by exact model ID (e.g., `gpt-4o-2024-08-06`), never an alias |
| Inputs | Sourced from corpus fixtures, never live data |
| Network | Disabled unless `allow_network: true` on the task |
| Temperature | 0 (or corpus-specified fixed value) |
| Tool outputs | Mocked or sandboxed; no real side effects |
| Time | Injected fixed timestamps where the agent uses clock data |

If a task cannot be made deterministic (e.g., requires live data), it is excluded from the certification corpus and used only for manual smoke testing.

### Benchmark Result Structure

```json
{
  "benchmark_run_id": "br-20260912-abc123",
  "workload_id": "incident-triage-agent",
  "manifest_version": 5,
  "corpus_id": "incident-triage-corpus",
  "corpus_version": 3,
  "model": "gpt-4o-2024-08-06",
  "environment": {
    "sandbox_image": "hiveplane/sandbox:0.2.0",
    "runtime_adapter": "langgraph-adapter:1.3.0",
    "control_plane_version": "0.2.0"
  },
  "started_at": "2026-09-12T10:00:00Z",
  "finished_at": "2026-09-12T10:04:32Z",
  "tasks": [
    {
      "task_id": "task-001",
      "status": "pass",
      "latency_ms": 4200,
      "tokens": 1850,
      "trace_id": "tr-001",
      "critical": false
    },
    {
      "task_id": "task-002",
      "status": "pass",
      "latency_ms": 12800,
      "tokens": 3200,
      "trace_id": "tr-002",
      "critical": true
    },
    {
      "task_id": "task-003",
      "status": "fail",
      "latency_ms": 31000,
      "tokens": 5100,
      "trace_id": "tr-003",
      "critical": false,
      "failure_reason": "rubric: missing 'contributing factors' criterion"
    }
  ],
  "aggregate": {
    "total": 3,
    "passed": 2,
    "failed": 1,
    "pass_rate": 0.667,
    "critical_failures": 0,
    "p50_latency_ms": 8500,
    "p95_latency_ms": 24000,
    "total_tokens": 10150,
    "estimated_cost_usd": 0.08
  }
}
```

## Certification Engine

### Purpose

Evaluate benchmark results against certification thresholds and assign a certification status. Produce a signed attestation that is stored immutably.

### Certification Statuses

| Status | Meaning | Where it can run |
|--------|---------|------------------|
| `uncertified` | Registered, never benchmarked | Sandbox only |
| `provisional` | Passed benchmark at staging threshold | Staging |
| `certified` | Passed benchmark at production threshold + survived N runs without regression | Production |
| `quarantined` | Failed re-certification or drifted below threshold | Runs blocked |

### Certification Thresholds

Thresholds are defined per workload type and scoped to a target context:

```yaml
certification_thresholds:
  staging:
    min_pass_rate: 0.70
    max_critical_failures: 2
    max_p95_latency_ms: 60000
  production:
    min_pass_rate: 0.85
    max_critical_failures: 0
    max_p95_latency_ms: 30000
    min_production_runs_survived: 50
```

A workload achieves `provisional` by passing the staging threshold. It achieves `certified` by passing the production threshold **and** surviving `min_production_runs_survived` production runs (while provisional) without a regression event.

### Evaluation Algorithm

```
function evaluate(benchmark_result, thresholds, target_context):

  if benchmark_result.aggregate.critical_failures > thresholds[target_context].max_critical_failures:
    return FAIL  # critical failures are hard blocks

  if benchmark_result.aggregate.pass_rate < thresholds[target_context].min_pass_rate:
    return FAIL

  if benchmark_result.aggregate.p95_latency_ms > thresholds[target_context].max_p95_latency_ms:
    return FAIL

  if target_context == production:
    if workload.production_runs_since_provisional < thresholds.production.min_production_runs_survived:
      return DEFER  # passed benchmark but hasn't survived enough real runs yet

  return PASS
```

### Status Transitions

```
                    ┌────────────────┐
                    │  uncertified   │
                    └───────┬────────┘
                            │ pass staging threshold
                            ▼
                    ┌────────────────┐
                    │   provisional  │◀─────────────────┐
                    └───────┬────────┘                  │
                            │ pass prod threshold       │ re-cert pass
                            │ + N survived runs         │
                            ▼                           │
                    ┌────────────────┐           ┌──────┴───────┐
                    │   certified    │──────────▶│  quarantined │
                    └───────┬────────┘  fail     └──────────────┘
                            │ re-cert              ▲
                            │ pass                 │
                            ▼                      │
                    ┌────────────────┐             │
                    │   certified    │  drift      │
                    │   (renewed)    │─────────────┘
                    └────────────────┘
```

### Signed Attestation

Each certification produces a signed attestation stored immutably (append-only in the state store, D7). The attestation binds the benchmark version, model identity, eval results, and environment:

```json
{
  "attestation_id": "att-20260912-xyz789",
  "workload_id": "incident-triage-agent",
  "manifest_version": 5,
  "benchmark_run_id": "br-20260912-abc123",
  "corpus_id": "incident-triage-corpus",
  "corpus_version": 3,
  "model": "gpt-4o-2024-08-06",
  "status": "certified",
  "target_context": "production",
  "eval_summary": {
    "pass_rate": 0.917,
    "critical_failures": 0,
    "p95_latency_ms": 24000,
    "tasks_passed": 11,
    "tasks_failed": 1
  },
  "timestamp": "2026-09-12T10:04:32Z",
  "environment": {
    "sandbox_image": "hiveplane/sandbox:0.2.0",
    "runtime_adapter": "langgraph-adapter:1.3.0",
    "control_plane_version": "0.2.0"
  },
  "signer": {
    "identity": "certification-service@hiveplane",
    "key_id": "hp-signing-key-01",
    "signature": "base64..."
  },
  "previous_attestation_id": "att-20260901-lmn456"
}
```

**Immutability:** Attestations are written once. Corrections create a new attestation that supersedes the previous one (linked via `previous_attestation_id`). The old attestation is never modified or deleted.

**Signing:** The certification service holds a signing key (or references an external KMS). The signature covers the canonical JSON serialization of all fields except `signature` itself. Verification is a public operation — any consumer can validate that an attestation was produced by HivePlane's certification service.

## Promotion Gate

### Purpose

Refuse to promote a workload manifest change to production until re-certification passes. Compare the new certification against the previous one and block regressions (DD-11).

### Promotion Flow

1. An operator submits a manifest change (new prompt, model, tool, or orchestration change).
2. The registry creates a pending manifest version (not yet promoted to production).
3. The promotion gate triggers a benchmark run against the new manifest version.
4. The certification engine evaluates the result.
5. If the result **passes**, the promotion gate compares it against the previous certification (regression diff).
6. If **no regressions** are found, the manifest version is promoted to production.
7. If **regressions** are found, promotion is blocked and the regression diff is presented.

### Regression Diff

The regression diff compares the new benchmark result against the previous certification's benchmark result, task by task:

```
Regression Diff: incident-triage-agent v4 → v5
═══════════════════════════════════════════════

  Tasks: 12 total
  Passed before: 11 | Passed now: 9
  Regressed: 2 | Improved: 0

  REGRESSED TASKS:

  ┌──────────┬──────────┬──────────┬─────────────────────────────┐
  │ Task     │ Before   │ Now      │ Failure reason              │
  ├──────────┼──────────┼──────────┼─────────────────────────────┤
  │ task-007 │ pass     │ fail     │ action_audit: forbidden      │
  │          │          │          │ action "restart service-a"   │
  │          │          │          │ detected                     │
  ├──────────┼──────────┼──────────┼─────────────────────────────┤
  │ task-009 │ pass     │ fail     │ rubric: missing 'root cause' │
  │          │          │          │ criterion                    │
  └──────────┴──────────┴──────────┴─────────────────────────────┘

  Replay traces:
    task-007: https://hiveplane/runs/tr-007-replay
    task-009: https://hiveplane/runs/tr-009-replay

  Verdict: BLOCKED — 2 regressions detected.
  Action: fix and re-certify, or request override (requires approval).
```

### Override Path

Promotion blocks can be overridden, but overrides are:
- Explicit (a named operator requests it with a reason)
- Audited (recorded in the audit log with attestation IDs)
- Visible (surfaced in the certification dashboard and fleet review)
- Rare (the override rate itself is a metric — a high override rate triggers a process review)

### Certification Is Necessary But Not Sufficient

Certification gates production admission, but runtime enforcement remains active (DD-15). Budget limits, policy checks, sandbox isolation, and approval flows are still enforced on every run, certified or not.

## Drift Detector

### Purpose

Schedule periodic re-certification for production agents. If performance decays below threshold, auto-quarantine the agent and notify the owning team. Drift is measured against the agent's own certification baseline, not a global standard (DD-12).

### Drift Detection Strategies

| Strategy | Trigger | Description |
|----------|---------|-------------|
| **Scheduled re-cert** | Cron (e.g., every 7 days) | Re-runs the full benchmark corpus against the live manifest version. Compares pass rate and per-task results against the baseline certification. |
| **Failure-spike-triggered** | Production failure rate exceeds threshold | If the rolling production failure rate exceeds 2x the baseline, an immediate re-cert is triggered. |
| **Continuous sampling** | Every Nth production run is scored against its expected outcome | Lightweight: not a full benchmark run, but samples production runs and checks them against known-good patterns. |

### Drift Measurement

Drift is measured by comparing the current re-certification result against the **baseline certification** — the attestation that granted the current `certified` status:

```
function detect_drift(current_result, baseline_attestation):

  delta_pass_rate = baseline_attestation.eval_summary.pass_rate - current_result.aggregate.pass_rate
  new_failures = tasks_that_passed_in_baseline_but_fail_now(current_result, baseline_attestation)

  if delta_pass_rate > DRIFT_THRESHOLD:  # e.g., 0.10 (10 percentage points)
    return DRIFT_DETECTED

  if any(t.critical for t in new_failures):
    return DRIFT_DETECTED  # any critical task regression is drift

  if len(new_failures) > MAX_NEW_FAILURES:  # e.g., 2
    return DRIFT_DETECTED

  return NO_DRIFT
```

### Auto-Quarantine

When drift is detected:
1. The workload's certification status transitions to `quarantined`.
2. The registry blocks all new runs for this workload (production and staging).
3. The owning team is notified (via result fan-out, D15) with a link to the regression diff and replay traces.
4. The workload remains quarantined until a human initiates re-certification after a fix, or the manifest is rolled back to the previous certified version.

### Drift Detector Configuration

```yaml
drift_detection:
  schedule: "0 3 * * 1"  # weekly, Monday 3 AM
  failure_spike_multiplier: 2.0
  failure_spike_window_minutes: 60
  drift_threshold_pass_rate: 0.10
  max_new_failures: 2
  continuous_sample_rate: 0.05  # 5% of production runs sampled
  auto_quarantine: true
  notify_channels:
    - slack:#incident-response-team
    - webhook:https://hooks.example.com/hiveplane-alerts
```

## Certification API

### REST Endpoints

```
POST   /certifications
  Body: { workload_id, manifest_version, target_context, corpus_id? }
  → 202 Accepted: { certification_id, benchmark_run_id, status: "running" }

GET    /certifications
  Query: ?workload_id=...&status=...&limit=...&offset=...
  → 200: { items: [...], total, limit, offset }

GET    /certifications/{id}
  → 200: { attestation, benchmark_result, regression_diff? }

GET    /certifications/compare/{v1}/{v2}
  → 200: { regression_diff, v1_attestation, v2_attestation }

POST   /certifications/{id}/override
  Body: { reason, operator }
  → 200: { promoted: true, override_record_id }
```

### CLI

```
hiveplane certify <workload> [--context staging|production] [--corpus <corpus_id>]
  → triggers a benchmark run and certification evaluation

hiveplane certs list [--workload <id>] [--status <status>]
  → lists certifications

hiveplane certs show <id>
  → shows attestation, eval results, and regression diff if applicable

hiveplane certs compare <v1> <v2>
  → shows regression diff between two certification versions
```

## Data Model

```
certifications
  id              TEXT PK
  workload_id     TEXT FK
  manifest_version INTEGER
  benchmark_run_id TEXT FK
  corpus_id       TEXT
  corpus_version  INTEGER
  model           TEXT
  status          TEXT  -- uncertified | provisional | certified | quarantined
  target_context  TEXT
  pass_rate       REAL
  critical_failures INTEGER
  p95_latency_ms  INTEGER
  timestamp       TIMESTAMPTZ
  signer_key_id   TEXT
  signature       TEXT
  previous_attestation_id TEXT
  superseded_by   TEXT  -- nullable, set when a newer attestation supersedes this one

benchmark_runs
  id              TEXT PK
  workload_id     TEXT FK
  manifest_version INTEGER
  corpus_id       TEXT
  corpus_version  INTEGER
  model           TEXT
  environment     JSONB
  started_at      TIMESTAMPTZ
  finished_at     TIMESTAMPTZ
  status          TEXT  -- running | completed | failed
  result          JSONB  -- full benchmark result

benchmark_task_results
  id              TEXT PK
  benchmark_run_id TEXT FK
  task_id         TEXT
  status          TEXT  -- pass | fail
  latency_ms      INTEGER
  tokens          INTEGER
  trace_id        TEXT
  critical        BOOLEAN
  failure_reason  TEXT

certification_overrides
  id              TEXT PK
  certification_id TEXT FK
  operator        TEXT
  reason          TEXT
  timestamp       TIMESTAMPTZ
```

## Integration Points

| Component | Integration |
|-----------|-------------|
| Registry (D3) | Checks certification status at run admission; refuses production runs unless `certified` |
| Execution Sandbox (D11) | Benchmark runs execute inside the sandbox with pinned environment |
| Runtime Adapter (D6) | Adapter executes benchmark tasks through the normal execution path |
| State Store (D7) | Attestations and benchmark results stored immutably |
| Telemetry (D8) | Benchmark runs emit traces and metrics with `benchmark=true` label |
| Operator UI (D9) | Certification dashboard: status, pass rates, trends, quarantine history |
| Trigger Service (D12) | Checks certification status before firing a triggered run |
| Agent Health (D16) | Drift indicators feed into health calculation; drift → health degradation → quarantine |
| Result Fan-out (D15) | Drift/quarantine notifications delivered to owning team |

## Open Questions

- **Evaluator model for rubric checks:** should the rubric evaluator be pinned per corpus or globally? If globally, a rubric evaluator upgrade could change pass/fail semantics across all corpora.
- **Corpus maintenance:** who owns the corpus? The workload owner or a separate QA function? How are corpus updates versioned and rolled out?
- **Partial re-certification:** if a manifest change only affects one tool, can we re-certify only the tasks that exercise that tool, or must we always run the full corpus?
- **Baseline drift over time:** should the baseline certification be re-anchored periodically (e.g., after a corpus update), and if so, how do we distinguish "legitimate baseline shift" from "gradual drift"?
- **Override limits:** should there be a maximum number of overrides per time window before the certification pipeline is considered compromised?
- **Multi-model certification:** if a workload supports multiple models (fallback strategy), does each model need independent certification?

## See Also

- [PRD 01: Why](../prd/01-why.md) — the certification thesis
- [PRD 02: Architecture](../prd/02-architecture.md) — certification pipeline in the system architecture
- [PRD 05: Features](../prd/05-features.md) — certification feature breakdown
- [Execution Sandbox Design](execution-sandbox-design.md) (D11) — isolated benchmark execution
- [Registry Service Design](registry-service-design.md) (D3) — certification status enforcement at admission
- [Agent Health Design](agent-health-design.md) (D16) — drift → health degradation → quarantine
- [Trigger Service Design](trigger-service-design.md) (D12) — certification check before trigger fires
- [Design Decisions](design-decisions.md) — DD-09, DD-10, DD-11, DD-12, DD-15
