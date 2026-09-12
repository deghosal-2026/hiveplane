# HivePlane Docs

Documentation for the HivePlane project — the control plane for production agent fleets.

**HivePlane is the certification pipeline.** Agents must be **certified** against a reproducible benchmark before they operate in production, and re-certified when they drift. No other agent platform gates production on evidence. This is not a feature — it is the thesis.

**Current release:** v0.1.0 (in development) — Seedling. The thesis ships here: register → certify → gate → run → intervene → deliver.

## Sections

| Directory | Purpose |
|-----------|---------|
| [prd/](prd/) | Product Requirements Documents (9 files) — why, architecture, landscape, users, features, security, metrics, risks, roadmap |
| [design/](design/) | Subsystem design documents (16 files) + design decisions |
| [workloads/](workloads/) | Agent workload manifest authoring: format spec and contribution guide |
| [wbs/](wbs/) | Work breakdown structure, organized by version |
| [field-test/](field-test/) | Field test plans and reports, organized by version |
| [release/](release/) | Release notes, organized by version |

## Quick Links

### PRDs

- [PRD 01: Why](prd/01-why.md) — Problem statement and motivation
- [PRD 02: Architecture](prd/02-architecture.md) — System architecture overview
- [PRD 03: Landscape](prd/03-landscape.md) — Competitive and adjacent landscape
- [PRD 04: Users and CUJs](prd/04-users-and-cujs.md) — Users and critical user journeys
- [PRD 05: Features](prd/05-features.md) — Feature breakdown by version
- [PRD 06: Security Baseline](prd/06-security-baseline.md) — Security considerations and threats
- [PRD 07: Success Metrics](prd/07-success-metrics.md) — How we measure success and release gates
- [PRD 08: Risks](prd/08-risks.md) — Risks, hard parts, mitigations
- [PRD 09: Roadmap](prd/09-roadmap.md) — Version roadmap and timeline

### Design Documents

- [Design Decisions](design/design-decisions.md) — DD-01 through DD-15
- [Workload Manifest Design](design/workload-manifest-design.md) — The stable workload contract
- [Run Lifecycle Design](design/run-lifecycle-design.md) — Run state machine
- [Registry Service Design](design/registry-service-design.md) — Desired state, validation, fleet catalog
- [Policy Engine Design](design/policy-engine-design.md) — Tool permissions, approval rules, escalation
- [Budget Enforcement Design](design/budget-enforcement-design.md) — Per-run and per-day budgets
- [Runtime Adapter Design](design/runtime-adapter-design.md) — The adapter boundary and conformance contract
- [State Store Design](design/state-store-design.md) — Persistent run state and audit history
- [Telemetry Design](design/telemetry-design.md) — OTel traces, metrics, logs, audit events
- [Operator UI Design](design/operator-ui-design.md) — Fleet dashboard, run detail, spend views

### Design Documents (New Capabilities)

- [Certification Pipeline](design/certification-pipeline-design.md) — Benchmark runner, certification engine, signed attestation, promotion gate, drift detector
- [Execution Sandbox](design/execution-sandbox-design.md) — Isolated execution context, resource caps, egress restriction, tool-call routing
- [Trigger Service](design/trigger-service-design.md) — Webhook/alert/cron/PR ingress, trigger rule matching, dedup, idempotency
- [MCP Tool Registry](design/mcp-tool-registry-design.md) — MCP tool onboarding (mcp-fabric), trust levels, stable tool IDs
- [Cost Service](design/cost-service-design.md) — Spend attribution, cost-per-completed-task, waste detection, ROI flags
- [Result Fan-out](design/result-fanout-design.md) — Delivery to Slack/Teams/Jira/PR/webhook with trace + attestation links
- [Agent Health](design/agent-health-design.md) — Readiness, failure rate, SLO status, drift indicators as fleet signals

### Reference Docs

- [Workload Manifest Format Spec](workloads/manifest-format-spec.md) — The stable contract (all fields)
- [Contributing Workloads](workloads/CONTRIBUTING.md) — How to add a workload
- [Adapters](ADAPTERS.md) — Adapter contract, conformance suite, sandbox + shaping + model-binding
- [Observability](observability.md) — Signals, agent health model, certification metrics, cost showback
- [User Guide](USER_GUIDE.md) — Operator guide

### Versioned Artifacts

- [v0.1.0 WBS Index](wbs/v0.1.0/wbs-v0.1.0-index.md)
- [v0.1.0 Field Test Plan](field-test/v0.1.0/field-test-plan.md)
- [v0.1.0 Field Test Report](field-test/v0.1.0/FIELD_TEST_REPORT.md)
- [v0.1.0 Release Notes](release/v0.1.0/release-notes.md)

## Conventions

- **BLUF** — Bottom Line Up Front in major documents
- **Exit gates** — standardized checklists for milestone completion
- **Design decisions** — centralized in `design/design-decisions.md`, referenced by DD-NN
- **Versioned directories** — each documentation type is organized by version
- **PRDs are at `docs/prd/`** — not `docs/design/prd/`
