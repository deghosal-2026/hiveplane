# PRD 02: Architecture

## TLDR

HivePlane is a FastAPI control plane backed by PostgreSQL, with a trigger/ingress layer, pluggable runtime adapters, an execution sandbox, a context-aware policy engine, budget enforcement, an MCP tool registry, a **certification pipeline** (benchmark + eval gate + attestation + drift detection), an OpenTelemetry telemetry pipeline, a cost/showback service, a result fan-out service, and a React operator UI. It runs locally on Docker Compose before it claims scale.

## Components

### Control Plane Core

1. **Trigger & Ingress Service** — accepts webhooks, alert events, GitHub PR events, and cron ticks; matches them to workload trigger rules; starts runs automatically. Handles dedup and idempotency.

2. **Registry Service** — stores agent definitions, owners, budgets, policies, runtime adapter type, trigger rules, certification status, and metadata. The registry refuses to admit a workload to a production context unless its certification status is `certified`.

3. **Execution API** — accepts tasks (manual or triggered), exposes run status, and serves intervention actions.

4. **Runtime Adapters** — translate between control-plane concepts and actual runtime frameworks (LangGraph, raw Python workers). Adapters report state transitions, tool calls, and usage; they do not decide policy.

5. **Execution Sandbox** — isolates destructive or production-affecting runs with per-run resource caps (memory, CPU, wall-clock, output size). Routes tool calls through the policy boundary. Shaping layer filters, truncates, and budgets large tool outputs before they reach the agent context window.

6. **Policy Engine** — evaluates context-aware tool permissions (staging vs. production, data sensitivity, blast-radius scoring), budget thresholds, and approval rules. Every decision carries a reason and originating rule. Supports team policy packs (versioned, distributable).

7. **Budget & Cost Service** — enforces per-run and per-day budgets at admission and on usage events; attributes spend to teams and agents; computes cost-per-completed-task, waste detection, and ROI flags.

8. **MCP Tool Registry** — onboards tools via the MCP layer (built on mcp-fabric); assigns trust levels and stable tool IDs; classifies read-only vs. destructive. Workloads reference tools by ID; policy decides who may call them and under what conditions.

9. **State Store** — persistent run state, desired state, audit history, policy outcomes, certification records, and operator actions. PostgreSQL as system of record.

10. **Telemetry Pipeline** — OTel traces, Prometheus metrics, structured logs, and audit events. Agent health signals: readiness, recent failure rate, SLO status, drift.

11. **Result Fan-out Service** — on completion/failure/escalation, pushes results to Slack, Teams, Jira, GitHub PR comments, or webhooks with trace links.

12. **Operator UI** — fleet dashboard, run detail pages, approval queue, certification dashboard, spend/showback views, agent health, and incident-friendly search.

### Certification Pipeline

13. **Benchmark Runner** — runs a workload against its benchmark corpus in a controlled, reproducible environment. Each task in the corpus has a known expected outcome and a deterministic pass/fail check. Benchmark runs are isolated from production — they execute in the sandbox with fixed model, fixed inputs, and no network side effects unless explicitly allowed.

14. **Certification Engine** — evaluates benchmark results against certification thresholds (pass rate, no critical failures, latency bounds). Assigns a certification status: `uncertified` → `provisional` → `certified` → `quarantined`. Produces a signed attestation: benchmark version, model, eval results, timestamp, environment, signer.

15. **Promotion Gate** — refuses to promote a workload manifest change to production until re-certification passes. Compares new certification against the previous one; blocks regressions. Integrates with agent-eval-forge-style harnesses for the eval execution.

16. **Drift Detector** — schedules periodic re-certification for production agents. If performance decays below threshold, the agent is auto-quarantined and the owning team is notified. Drift is measured against the agent's own certification baseline, not a global standard.

## High-Level Flow

```
                    CERTIFICATION PIPELINE
                    ┌─────────────────────────────────────────────┐
                    │                                             │
  Register/Change ──> Benchmark Runner ──> Certification Engine   │
                    │        │                    │               │
                    │        │               pass/fail            │
                    │        │            ┌───────┴───────┐       │
                    │        │         attest      quarantine    │
                    │        │            │               │       │
                    │     replay trace    │               │       │
                    │                     ▼               ▼       │
                    │            Promotion Gate      Drift Detector│
                    │            (block/allow)     (periodic re-cert)│
                    └─────────────────────────────────────────────┘
                                          │
                              certified?  │
                            ┌─────────────┴─────────────┐
                            │ yes                       │ no
                            ▼                           ▼
PRODUCTION FLOW              │                 SANDBOX ONLY
                             │
Webhook/Alert/Cron ──> Trigger & Ingress ──┐
                                           │
Client ──> Execution API ──> Registry (resolve + check certification)
                 │
                 ├──> Policy Engine (context-aware permissions, approvals, budget)
                 │
                 ├──> MCP Tool Registry (resolve tool IDs, trust levels)
                 │
                 ├──> Execution Sandbox ──> Runtime Adapter ──> Agent Runtime
                 │        │                      │
                 │        │                      └──> state + usage + tool calls
                 │        │
                 │        └──> tool-output shaping (filter, truncate, budget)
                 │
                 ├──> Budget & Cost Service (enforce + attribute + showback)
                 ├──> State Store (desired + actual state, audit)
                 ├──> Telemetry Pipeline (traces, metrics, logs, health)
                 │            │
                 │            └──> Operator UI (fleet, runs, certs, spend, health)
                 │
                 └──> Result Fan-out (Slack/Teams/Jira/PR/webhook)
```

## Design Principles

- agent runtimes stay pluggable
- the workload contract stays stable
- **production is earned, not assumed** — no agent runs in production without certification
- operator actions are auditable
- policy should be visible, not hidden in code
- tool outputs are shaped at the boundary, not inside the agent
- changes to live workloads are gated by eval, not by hope
- certifications are reproducible, signed, and versioned
- the system must be useful locally before it claims scale

## See Also

- [Workload manifest design](../workload-manifest-design.md)
- [Run lifecycle design](../run-lifecycle-design.md)
- [Policy engine design](../policy-engine-design.md)
- [Budget enforcement design](../budget-enforcement-design.md)
- [Runtime adapter design](../runtime-adapter-design.md)
- [Telemetry design](../telemetry-design.md)
- [Design decisions](../design-decisions.md)
