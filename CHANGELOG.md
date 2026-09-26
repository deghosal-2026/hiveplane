# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - Unreleased

The Complete Fleet OS release: tenancy, autonomy (triggers, pipelines, GitOps), the
immune system (drift, quarantine, promotion gate), and fleet scale-out. In progress.

### Added (M25-01 — tenancy foundation)

- **Tenancy package (`hiveplane.tenancy`)** — `Tenant`/`Team`/`Membership` domain models
  (`Role`: admin/approver/viewer), `TenantScopeError`, and a frozen `TenantContext`
  (`SYSTEM_CONTEXT`, `DEFAULT_CONTEXT`) threaded explicitly through every store call.
  Reserved ids: `default` (legacy backfill) and `system`.
- **Tenant store** — protocol + in-memory + PostgreSQL implementations for
  tenants/teams/memberships, tenant-qualified uniqueness, and composite team identity
  `(tenant_id, team_id)`.
- **Tenant-scoped schema** — every existing table gains a non-null `tenant_id` (plus
  `team_id`/`attribution_key` on run/usage/cost tables); composite `(parent_id, tenant_id)`
  foreign keys and tenant-qualified unique constraints throughout.
- **Migration `0003`** — forward-only: seeds `default`/`system` tenants, backfills legacy
  rows to `default`, deletes orphan runs and dangling workload references, then enforces
  the new constraints. Auto-migration on startup preserved; verified against Postgres 16
  on fresh and real v0.1.0 databases.
- **Store-layer isolation** — every durable store (runs, registry, certification, budget,
  approvals, policy packs, audit) enforces tenant scoping: reads outside the acting tenant
  look like the record does not exist, and writes that cross a tenant boundary raise
  `TenantScopeError`. Runs are attributed to the submitting tenant.
- **API tenant resolution** — `X-Hiveplane-Tenant` / `X-Hiveplane-Team` headers select the
  acting tenant (default tenant when absent). This is plumbing, not an auth boundary;
  signed identities arrive in M45.
- **Test database override** — `HIVEPLANE_DATABASE__*` env vars are honored by the
  Postgres-gated tests so they can run against a throwaway database (as CI does).

### Changed

- Version bumped to 0.2.0 (in-progress release line).

### Added (M25-02..M25-11 — fleet-control data models)

- **`hiveplane.fleet`** — the frozen v0.2.0 model surface: triggers (source,
  event filter, dedup, cooldown, rate limit, task template, admission rule,
  events/runs/DLQ), pipelines (validated DAG with edges, handoff mappings,
  per-step gates, budget, run linkage), versioned policy packs with lint status
  and policy decision records, secrets with rotation metadata and injection
  references, distributed workers with leases and heartbeats, artifacts with
  retention policies, metering events and day/week/month cost periods, and
  desired-state reconciliation (desired specs, reconcile state, drift records).
- **20 tenant-scoped tables** for the above, with composite foreign keys where a
  real parent-child relationship exists and tenant-qualified uniques.
- **Migration `0004`** — forward-only and idempotent; auto-migration on startup
  preserved. Verified up/down against PostgreSQL 16.
- **Tests** — model validation (malformed trigger/pipeline/policy definitions
  rejected with actionable errors), schema coverage, tenant scoping, and
  migration up/down. Coverage ≥ 95% with a database (as CI runs).

### Added (M26 — desired-state reconciliation / GitOps)

- **`hiveplane.reconcile` package** — the Kubernetes-defining controller:
  - **Loader** (`loader`) — validates a fleet manifest set (workloads, policy packs,
    triggers, budgets) from a local directory or a read-only git source pinned to a ref;
    a bad revision is rejected whole with nothing applied and content hashes are
    deterministic across formatting.
  - **Observer / differ / conflict policy** — reads actual state through existing
    services and diffs it field by field. Declarative `spec.*` fields are
    declared-wins; runtime, certification-status, and operator-pinned fields are
    observed-wins and recorded as drift rather than overwritten.
  - **Planner + guardrails** — classifies actions additive/soft/destructive and gates
    destructive ones: they require explicit permission, an empty desired set cannot
    cascade to mass deregistration, the first reconcile needs confirmation, and
    destructive actions are rate-limited per pass.
  - **Executor** — applies actions only through `RegistryService` (register, update,
    re-certify, deregister, quarantine, policy-version enforcement), so reconcile
    cannot bypass certification, policy, or budget enforcement.
  - **Controller** — one pass is observe → diff → plan → act → record, with a dry-run
    `plan` mode that mutates nothing.
- **Single-writer safety** — per-source in-memory lock and a PostgreSQL advisory lock
  keyed by source id, so a second replica stays passive and cannot double-act.
- **Git change detection** — poll-on-revision-change and HMAC-SHA256 webhook triggers;
  git credentials resolve from a secret reference, never inline, and the source is
  never written back to.
- **Migration `0005`** — append-only `reconcile_runs` history table (revision, mode,
  outcome, counts, timestamps); forward-only and idempotent.
- **API + CLI** — `GET /reconcile/{source}` (status), `/runs`, `/drift`,
  `POST /reconcile/{source}/plan|apply`, and `hiveplane reconcile status|plan|apply`.
- **Tests** — idempotency, add/remove/update paths, dry-run safety, concurrent-controller
  safety, guardrails, conflict policy, and Postgres store/migration round-trips.
  Coverage ≥ 95% with a database.

## [0.1.0] - 2026-09-25

The first release: the certified control loop. Register agents, certify them against a
reproducible benchmark, admit only certified agents to production, run them under
budget/policy/sandbox, intervene on live runs, deliver results, and observe the fleet —
all locally on Docker Compose with real workloads.

### Added

- **Foundation & data models** — workload manifest (`hiveplane/v1`), run state machine,
  typed core models, settings, and the MCP tool registry with stable tool IDs and trust levels.
- **Registry service** — agent registration, desired-state validation, fleet catalog, tool
  and trigger records, and certification attestation storage.
- **Certification pipeline (the thesis)** — reproducible benchmark runner, certification
  engine, signed (Ed25519) attestations verified on read, promotion gate, and drift detector.
  Production admission requires a valid, unexpired certification.
- **Run lifecycle & execution API** — task submission, persistent run state
  (queued → running → paused/completed/failed), pause/resume/cancel controls, admission
  gates, and durable pause/resume across restarts.
- **Policy engine & approvals** — deny-by-default tool policy, per-tool authorization,
  escalation, and a human approval queue with attributed operator actions.
- **Budget enforcement** — per-run and per-day budgets enforced before expensive work,
  with model pricing and usage accounting.
- **Execution sandbox & tool-output shaping** — isolated execution context with resource
  caps and restricted egress; tool outputs inspected, bounded, and injection-scanned.
- **Runtime adapters & conformance** — raw-worker and LangGraph adapters plus a conformance
  suite; model-identity binding reported from actual inference (model-swap defense).
- **State store & persistence** — PostgreSQL system of record with Alembic migrations and
  a tamper-evident, chained-hash audit log.
- **Telemetry & observability** — OpenTelemetry-native traces, metrics, logs, and audit
  events; fleet metrics and trace-linked debug context; Docker Compose stack with Tempo,
  Prometheus, and Grafana.
- **CLI & operator UI** — `hiveplane` CLI (register, validate, certify, submit, runs,
  approve) and a server-rendered operator UI (fleet list, run detail, approvals, spend).
- **Result fan-out** — delivery to Slack and generic webhooks with trace + attestation links.
- **Docker Compose reference stack** — one-command start; `.env` profiles for fake (hermetic),
  local (OMLX), and cloud (OpenAI) model backends.
- **Field test** — 10/10 scenarios and all acceptance criteria A1–A20 pass against the live
  stack; container-layer suite 25/25 green. See
  [Field Test Report](docs/field-test/v0.1.0/FIELD_TEST_REPORT.md).

### Security

- Deny-by-default tool policy and production admission gated on certification.
- Signed attestations verified on every read; persistent signing key.
- Secrets redacted from logs, traces, and audit events.
- Pre-release secret/dependency audit (trufflehog, `pip-audit`) clean — see
  [Security audit](docs/release/v0.1.0/security-audit.md) and [SECURITY.md](SECURITY.md).

### Known limitations

- Coverage gate is **92%** (local 93%; CI 95.45% with Postgres).
- Tier 2 platform coverage is one certified agent per framework (broader coverage deferred).
- Multi-tenant support, ROI dashboards, and Helm/cluster deployment deferred to v0.4.0.

[Unreleased]: https://github.com/deghosal-2026/hiveplane/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/deghosal-2026/hiveplane/releases/tag/v0.1.0
