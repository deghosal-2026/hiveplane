# PRD 02: Architecture

## TLDR

HivePlane is a FastAPI control plane backed by PostgreSQL, with pluggable runtime adapters, a policy engine, budget enforcement, an OpenTelemetry telemetry pipeline, and a React operator UI. It runs locally on Docker Compose before it claims scale.

## Components

1. **Registry Service** — stores agent definitions, owners, budgets, policies, runtime adapter type, and metadata.
2. **Execution API** — accepts new tasks, exposes run status, and serves intervention actions.
3. **Runtime Adapters** — translate between control-plane concepts and actual runtime frameworks like LangGraph or raw Python workers.
4. **Policy Engine** — evaluates tool permissions, budget thresholds, and approval rules.
5. **State Store** — persistent run state, desired state, audit history, policy outcomes, and operator actions.
6. **Telemetry Pipeline** — OTel traces, Prometheus metrics, structured logs, and audit events.
7. **Operator UI** — fleet dashboard, run detail pages, approval queue, spend views, and incident-friendly search.

## High-Level Flow

```
Client ──> Execution API ──> Registry (resolve workload)
                 │
                 ├──> Policy Engine (permissions, approvals, budget)
                 │
                 ├──> Runtime Adapter ──> Agent Runtime (LangGraph / worker)
                 │            │
                 │            └──> state + usage events
                 │
                 ├──> State Store (desired + actual state, audit)
                 └──> Telemetry Pipeline (traces, metrics, logs)
                              │
                              └──> Operator UI (fleet, runs, approvals, spend)
```

## Design Principles

- agent runtimes stay pluggable
- the workload contract stays stable
- operator actions are auditable
- policy should be visible, not hidden in code
- the system must be useful locally before it claims scale

## See Also

- [Workload manifest design](../workload-manifest-design.md)
- [Run lifecycle design](../run-lifecycle-design.md)
- [Design decisions](../design-decisions.md)
