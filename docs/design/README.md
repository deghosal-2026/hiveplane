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

## v0.2.0 Subsystem Designs (D21–D38)

The Complete Fleet OS extends the v0.1.0 subsystem designs below. New subsystems are grouped by capability area; where a v0.1.0 design is extended, the new doc is authoritative for v0.2.0 behavior.

| D# | Document | Topic | Milestones | Extends |
|----|----------|-------|------------|---------|
| D21 | [fleet-control-data-model-design.md](fleet-control-data-model-design.md) | Teams/tenants, triggers, pipelines, policies, secrets, workers, artifacts, cost periods, reconciliation state | M25 | D7 |
| D22 | [reconciliation-design.md](reconciliation-design.md) | Desired-state controller, GitOps, drift records, conflict policy | M26 | — |
| D23 | [trigger-service-v2-design.md](trigger-service-v2-design.md) | Trigger DSL, HMAC, cron, dedup/cooldown/rate-limit, admission rules, freeze, DLQ | M27–M28 | D12 |
| D24 | [orchestration-design.md](orchestration-design.md) | Pipelines, handoffs, per-step gates, task router, agent-as-tool, A2A | M29–M30 | D2 |
| D25 | [runtime-adapter-v2-design.md](runtime-adapter-v2-design.md) | Adapter contract v2, PydanticAI/OpenAI SDK/CrewAI, conformance v2, `wrap` | M31 | D6 |
| D26 | [certification-v2-design.md](certification-v2-design.md) | Promotion gate, regression diff, drift/quarantine, provenance, transparency log | M32–M35 | D10 |
| D27 | [learning-loop-design.md](learning-loop-design.md) | Feedback → corpus, online eval sampling, judge rubrics | M36 | D10 |
| D28 | [progressive-delivery-design.md](progressive-delivery-design.md) | Shadow runs, canary routing, auto-promote/abort, model experiments | M37–M38 | D10 |
| D29 | [defense-policy-v2-design.md](defense-policy-v2-design.md) | Injection defense, taint, egress, context-aware policy, packs, kill switch | M39–M40 | D4, D11 |
| D30 | [runtime-guards-design.md](runtime-guards-design.md) | Context-window budgets, spend velocity, retries, circuit breakers | M41 | D5 |
| D31 | [agent-health-slo-design.md](agent-health-slo-design.md) | Health model, SLO/error budget, burn throttle, probes, self-monitoring | M42–M43 | D16 |
| D32 | [mcp-registry-v2-design.md](mcp-registry-v2-design.md) | Live MCP transport, discovery, trust levels, allow-lists | M44 | D13 |
| D33 | [secrets-rbac-design.md](secrets-rbac-design.md) | Secret store, injection, rotation, RBAC-lite, access audit | M45 | — |
| D34 | [fleet-execution-design.md](fleet-execution-design.md) | Distributed workers, leases, scheduler, preemption, HA, chaos | M46–M48 | D2 |
| D35 | [cost-roi-v2-design.md](cost-roi-v2-design.md) | Showback, teams, periods, estimates, tier routing, cache, ROI | M49–M50 | D14 |
| D36 | [operator-experience-design.md](operator-experience-design.md) | Fan-out, notifications, approvals, UI v2, CLI, `ask`, incident, artifacts, replay | M51–M54, M60 | D9, D15 |
| D37 | [platform-api-design.md](platform-api-design.md) | Corpus tooling, API v2, SDK, agent-as-service, rate limits, plugins | M55–M56 | — |
| D38 | [reporting-tenancy-distribution-design.md](reporting-tenancy-distribution-design.md) | Digest, audit export, compliance, retention/PII, tenancy, Helm, supply chain | M57–M59 | D7 |

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
