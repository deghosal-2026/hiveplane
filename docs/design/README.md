# Design Documents

Design docs for HivePlane — organized by subsystem and PRD.

## PRD

| # | Document | Description |
|---|----------|-------------|
| 01 | [Why](../prd/01-why.md) | Problem statement and motivation |
| 02 | [Architecture](../prd/02-architecture.md) | System architecture overview |
| 03 | [Landscape](../prd/03-landscape.md) | Competitive and adjacent landscape |
| 04 | [Users and CUJs](../prd/04-users-and-cujs.md) | Users and critical user journeys |
| 05 | [Features](../prd/05-features.md) | Feature breakdown by version |
| 06 | [Security Baseline](../prd/06-security-baseline.md) | Security considerations and threats |
| 07 | [Success Metrics](../prd/07-success-metrics.md) | How we measure success and release gates |
| 08 | [Risks](../prd/08-risks.md) | Risks, hard parts, mitigations |
| 09 | [Roadmap](../prd/09-roadmap.md) | Version roadmap and timeline |

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
| D10 | [certification-pipeline-design.md](certification-pipeline-design.md) | Benchmark runner, certification engine, signed attestation, promotion gate, regression diff, drift detector, certification API |
| D11 | [execution-sandbox-design.md](execution-sandbox-design.md) | Isolated execution context, resource caps, restricted egress, sandbox lifecycle |
| D12 | [trigger-service-design.md](trigger-service-design.md) | Webhook/alert/PR/cron ingestion, trigger rule matching, dedup, watch mode, certification check |
| D13 | [mcp-tool-registry-design.md](mcp-tool-registry-design.md) | MCP tool onboarding, stable tool IDs, trust levels, tool health |
| D14 | [cost-service-design.md](cost-service-design.md) | Cost attribution, cost-per-completed-task, waste detection, ROI flags, showback views |
| D15 | [result-fanout-design.md](result-fanout-design.md) | Fan-out to Slack/Teams/Jira/PR/webhook, message format, delivery guarantees |
| D16 | [agent-health-design.md](agent-health-design.md) | Health signals, rolling-window health calculation, fleet aggregation, drift → quarantine |
| D17 | [llm-provider-design.md](llm-provider-design.md) | LLM provider seam (local/cloud/fake), agent invocation, runtime model identity |
| D18 | [durable-resume-design.md](durable-resume-design.md) | Startup recovery and restart-resume for paused/running runs |
| D19 | [benchmark-execution-design.md](benchmark-execution-design.md) | Adapter-backed certification: execute the real agent against the corpus |
| D20 | [corpus-fixture-coupling.md](corpus-fixture-coupling.md) | Corpus ↔ fixture ↔ model coupling: deterministic task contracts, replay keying, benchmark auto-approval, negative proof |

## Cross-Cutting

- [Execution Path Design](execution-path-design.md) — implementation blueprint for the run lifecycle, policy, budget, sandbox, shaping, and adapters
- [Design Decisions](design-decisions.md) — DD-01 through DD-15
- **Certification Pipeline** (D10) — benchmark runner, certification engine, signed attestation, promotion gate, regression diff, drift detector, certification API
- **Execution Sandbox** (D11) — isolation, resource caps, restricted egress, adapter integration
- **Trigger Service** (D12) — webhook/alert/PR/cron ingestion, rule matching, dedup, watch mode
- **MCP Tool Registry** (D13) — tool onboarding, trust levels, stable IDs, health
- **Cost Service** (D14) — attribution, CPCT, waste detection, ROI, showback
- **Result Fan-out** (D15) — multi-destination delivery, message format, delivery guarantees
- **Agent Health** (D16) — health signals, fleet aggregation, drift → quarantine path
