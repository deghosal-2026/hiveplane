# Design Documents

Design docs for HivePlane — organized by subsystem and PRD.

## PRD

| # | Document | Description |
|---|----------|-------------|
| 01 | [Why](prd/01-why.md) | Problem statement and motivation |
| 02 | [Architecture](prd/02-architecture.md) | System architecture overview |
| 03 | [Landscape](prd/03-landscape.md) | Competitive and adjacent landscape |
| 04 | [Users and CUJs](prd/04-users-and-cujs.md) | Users and critical user journeys |
| 05 | [Features](prd/05-features.md) | Feature breakdown by version |
| 06 | [Security Baseline](prd/06-security-baseline.md) | Security considerations and threats |
| 07 | [Success Metrics](prd/07-success-metrics.md) | How we measure success and release gates |
| 08 | [Risks](prd/08-risks.md) | Risks, hard parts, mitigations |
| 09 | [Roadmap](prd/09-roadmap.md) | Version roadmap and timeline |

## Subsystem Designs

| D# | Document | Topic |
|----|----------|-------|
| D1 | [workload-manifest-design.md](workload-manifest-design.md) | The stable workload contract: owner, runtime, tools, budget, approvals |
| D2 | [run-lifecycle-design.md](run-lifecycle-design.md) | Run state machine: queued → running → paused/completed/failed |
| D3 | [registry-service-design.md](registry-service-design.md) | Desired state, validation, fleet catalog |
| D4 | [policy-engine-design.md](policy-engine-design.md) | Tool permissions, approval rules, escalation outcomes |
| D5 | [budget-enforcement-design.md](budget-enforcement-design.md) | Per-run and per-day budgets, quota, cost-per-run |
| D6 | [runtime-adapter-design.md](runtime-adapter-design.md) | The adapter boundary and conformance contract |
| D7 | [state-store-design.md](state-store-design.md) | Persistent run state, desired state, audit history |
| D8 | [telemetry-design.md](telemetry-design.md) | OTel traces, metrics, logs, audit events |
| D9 | [operator-ui-design.md](operator-ui-design.md) | Fleet dashboard, run detail, approval queue, spend views |

## Cross-Cutting

- [Design Decisions](design-decisions.md) — DD-01 onward
