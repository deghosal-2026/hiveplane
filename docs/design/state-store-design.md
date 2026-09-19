# D7: State Store Design

> Status: partial. The schema and migrations exist (M18), and `PostgresRunStore` /
> `PostgresAuditLog` are implemented. However the app factory (`create_app`) still hardcodes
> in-memory stores for registry, policy packs, approvals, budget, and certifications, and
> migrations do not run on startup — so a fresh `docker compose up` is not durable. PostgreSQL
> wiring and auto-migration are tracked by #118, #125, #126, #128.

## Problem

Desired state, run state, and audit history must be durable enough to survive restarts and to answer "what happened?" after the fact (DD-05). After the PRD rewrite, the state store must also persist: certifications, attestations, tools, trigger rules, drift schedules, cost attributions, fan-out deliveries, and health signals — all the new entities introduced by the certification pipeline, triggers, MCP tool registry, cost showback, result fan-out, and agent health capabilities.

See [PRD 02: Architecture](../prd/02-architecture.md) § State Store and [PRD 05: Features](../prd/05-features.md).

## Entities

### Core Entities (existing)

| Entity | Contents |
|--------|----------|
| `workloads` | Current manifest, owner, team, certification status, timestamps |
| `workload_versions` | Append-only manifest history (including pending versions) |
| `runs` | Run identity, workload, caller, trigger origin, current state, target context, timestamps |
| `run_events` | Append-only transition log with attribution |
| `usage_events` | Token/tool usage for budget accounting (model, tokens, cost, tool calls) |
| `audit_log` | Operator actions and policy outcomes (tamper-evident) |
| `approvals` | Pending and resolved approval requests |

### New Entities

| Entity | Contents | Purpose |
|--------|----------|---------|
| `certifications` | Certification ID, workload, manifest version, benchmark corpus + version, status (`uncertified`/`provisional`/`certified`/`quarantined`), threshold, pass rate, critical failures, latency summary, timestamp, environment | Certification pipeline records (DD-09, DD-11) |
| `attestations` | Attestation ID, workload, manifest version, benchmark corpus + version, model identity, eval results (per-task pass/fail), threshold, status assigned, timestamp, environment, signer, signature, previous attestation ID | Immutable signed attestations (DD-10, T9) |
| `tools` | Tool ID, name, MCP server, trust level (`read_only`/`destructive`), description, parameters schema, registered at, registered by | MCP tool registry (PRD 05: tools & MCP) |
| `trigger_rules` | Trigger ID, workload, type (`webhook`/`alert`/`github_pr`/`cron`), match criteria, mode (`one-shot`/`watch`/`scheduled`), max concurrent, created at | Trigger rules per workload (PRD 05: triggers) |
| `drift_schedules` | Schedule ID, workload, re-cert interval, last re-cert run, next re-cert run, grace margin, status | Drift detection schedules (DD-12) |
| `cost_attributions` | Period, team, workload, total spend, completed tasks, failed tasks, escalated tasks, cost per completed task, waste, ROI flag | Cost showback records (PRD 05: cost & ROI) |
| `fan_out_deliveries` | Delivery ID, run ID, terminal state, destination type (`slack`/`teams`/`jira`/`github_pr_comment`/`webhook`), destination ref, message payload, delivery status, attempted at, delivered at, retry count | Result fan-out delivery records (PRD 05: result delivery) |
| `health_signals` | Signal ID, workload, readiness status, recent failure rate, SLO status (availability, quality, error budget remaining), drift indicator, last updated | Agent health model (PRD 05: observability & health) |
| `llm_provider_configs` | Config ID, provider (`ollama`/`openai`/`fake`), base URL, credential ref, default model identity, timeout, created at | LLM provider seam configuration (#107) |

## Entity Relationships

```
workloads ──< workload_versions
workloads ──< certifications ──< attestations
workloads ──< trigger_rules
workloads ──< drift_schedules
workloads ──< health_signals
workloads ──< runs ──< run_events
runs ──< usage_events ──> cost_attributions
runs ──< fan_out_deliveries
runs ──< approvals
tools ──< (referenced by workload manifests via tool_id)
audit_log (standalone, references runs, workloads, operators)
```

## Guarantees

- transitions are persisted before side effects are acknowledged
- event logs are append-only
- audit records are tamper-evident (chained hash or append-only with checksums)
- attestations are immutable — once written, never mutated; new certifications create new attestation records
- attestation signatures are verified on every read (T9) — requires a **persistent signing keypair** (see below)
- escalated tool calls awaiting re-dispatch are persisted so approval survives a restart (#129)
- queries by run, workload, owner/team, certification status, and time window
- fan-out delivery status is recorded but does not block terminal state
- health signals are updated on every run terminal transition and on readiness probe results
- cost attributions are computed continuously from usage events (not batch-only)
- trigger rules are persisted and versioned with the manifest

## Technology

PostgreSQL as the system of record. The schema exists (M18); the app factory must be wired to
use it and migrations must run on startup (#118).

## Migration Strategy

- Migrations are Alembic-managed (`alembic upgrade head`).
- The control plane runs migrations on startup (lifespan hook or container entrypoint) so a
  fresh `docker compose up` yields a working schema with no manual step (#118).
- `hiveplane init` bootstraps a fresh project against an already-migrated database.
- Migrations are idempotent and safe to re-run.

## Signing Keypair Management

- The attestation signing key is **persistent** — loaded from a configured file
  (`HIVEPLANE_CERTIFICATION__SIGNING_KEY_FILE`) or KMS, generated once if absent (#125).
- An ephemeral per-boot keypair is a defect: it invalidates all prior attestations on restart,
  breaking T9 and "attestation verified on read".
- Key rotation is explicit and audited; rotated keys must still verify historical attestations
  (retain public keys by attestation `signer`).

### Indexing Strategy

| Table | Index | Purpose |
|-------|-------|---------|
| `workloads` | `certification_status` | Fleet queries by cert status |
| `attestations` | `workload, timestamp DESC` | Attestation history per workload |
| `runs` | `workload, state, created_at` | Run queries by workload and state |
| `run_events` | `run_id, timestamp` | Transition log per run |
| `usage_events` | `run_id, workload, team, timestamp` | Budget and cost attribution queries |
| `cost_attributions` | `team, period, workload` | Showback queries |
| `fan_out_deliveries` | `run_id, delivery_status` | Fan-out delivery tracking |
| `health_signals` | `workload, last_updated` | Health dashboard queries |
| `trigger_rules` | `workload, type` | Trigger rule lookup |
| `drift_schedules` | `next_re_cert_run` | Drift detector scheduling |
| `tools` | `tool_id` (unique) | MCP tool registry lookup |

## Retention

| Entity | Retention | Reason |
|--------|-----------|--------|
| `workload_versions` | Indefinite | Audit / history |
| `attestations` | Indefinite | Audit / regression diff |
| `certifications` | Indefinite | Audit |
| `run_events` | 90 days | Debugging; older events summarized |
| `usage_events` | 90 days raw, aggregated indefinitely | Cost attribution needs history |
| `audit_log` | Indefinite | Compliance |
| `fan_out_deliveries` | 30 days | Delivery tracking; older not needed |
| `health_signals` | 30 days detailed, indefinite summarized | Trend analysis |

## Open Questions

- retention windows for events vs audit records
- partitioning strategy for high-volume usage events
- whether health signals should be in PostgreSQL or a time-series store
- attestation storage: PostgreSQL vs external signed storage (e.g., a transparency log)
- whether cost attributions are computed in real-time or batched per period

## See Also

- [Registry service](registry-service-design.md) — certifications, attestations, tools, trigger_rules, drift_schedules storage
- [Run lifecycle](run-lifecycle-design.md) — run_events, fan_out_deliveries, trigger-originated runs
- [Budget enforcement](budget-enforcement-design.md) — usage_events, cost_attributions
- [Telemetry](telemetry-design.md) — health_signals, metrics
- [Workload manifest](workload-manifest-design.md) — manifest shape that drives entity creation
- [Design decisions](design-decisions.md) — DD-05, DD-09, DD-10
