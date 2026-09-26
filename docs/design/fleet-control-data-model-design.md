# D21: Fleet Control Data Model Design

> Status: draft
>
> **Implementation status (M25-01, in progress):** tenancy package, ORM schema, and migration
> `0003` are landed. Store-layer `TenantContext` enforcement and API header resolution are the
> remaining scope — decisions and task sequence live in
> [`docs/plans/m25-01-tenancy-scoping.md`](../plans/m25-01-tenancy-scoping.md).

**Milestones:** M25 · **Extends:** D7

## Problem

v0.1.0's state store (D7) covers workloads, runs, events, usage, audit, approvals, certifications, attestations, tools, trigger rules, drift schedules, cost attributions, fan-out, and health. v0.2.0 adds first-class fleet primitives that cut across all of them: tenants/teams, triggers with admission rules, pipelines, policy packs, secrets, workers, artifacts, cost periods, and reconciliation state. If each subsystem invents its own tables, tenant scoping becomes inconsistent — a security defect — and every later milestone needs its own migration. M25 defines the frozen v0.2.0 model surface and one forward-only migration from v0.1.0.

## Overview

```
                         ┌──────────────────────────────────────────┐
                         │                 tenants                   │
                         │   ┌────────┐    ┌────────────────────┐   │
                         │   │ teams  │───▶│ memberships/api_keys│   │
                         │   └───┬────┘    └────────────────────┘   │
                         └───────┼──────────────────────────────────┘
              tenant_id scopes every row below
   ┌────────────┬───────┼──────────┬───────────┬──────────┬──────────┐
   ▼            ▼       ▼          ▼           ▼          ▼          ▼
workloads   triggers pipelines policy_packs secrets   workers   artifacts
   │            │        │          │           │          │          │
   ▼            ▼        ▼          ▼           ▼          ▼          ▼
 runs ◀── trigger_events │   policy_decisions │    worker_leases   run_artifacts
   │            ▼        ▼          ▼           ▼          │          ▼
   ▼        trigger_runs pipeline_runs secret_refs heartbeats retention_policies
usage_events ──▶ metering_events ──▶ cost_periods
desired_specs ──▶ reconcile_state ──▶ drift_records
```

## Design

### Tenancy and Attribution

`tenants` is the isolation boundary; `teams` are attribution and policy scope inside a tenant. Membership rows bind an operator or scoped API key to a role (`admin` / `approver` / `viewer`). Every fleet primitive carries `tenant_id`; every run records `tenant_id` + `team_id` + `attribution_key` so cost, policy, and audit resolve the same owner. `attribution_key` is the stable, human-readable key used by metering and chargeback.

### Triggers

A trigger stores `source` (`webhook` / `github` / `alertmanager` / `cron` / `watch`), a structured event `filter`, `dedup_key_template`, `cooldown_seconds`, `rate_limit`, `task_template` (typed payload→task mapping), `admission_rule`, `target_ref` (workload or pipeline), and `enabled`. History is append-only: `trigger_events` (received + dedup/cooldown/freeze outcome) and `trigger_runs` (decision + run linkage). Semantics are in D23.

### Pipelines

`pipelines` stores a versioned DAG: `nodes` (workload or gate), `edges` with `handoff_mapping` (output→input projection), per-step `gate`, and a `pipeline_budget`. `pipeline_runs` links parent and child run ids to a node, giving fan-in/fan-out without a second run lifecycle. See D24.

### Policy Packs and Decisions

`policy_packs` are versioned, inheritable bundles (`parent_pack_id`) with `lint_status` and a content hash. `policy_decisions` records each evaluated decision with `outcome`, `reason`, and `originating_rule_id`, so "why was this denied?" is answerable without re-deriving. The effective rule set is resolved at admission. See D29.

### Secrets

`secrets` stores ciphertext-at-rest only: `name`, `scope` (tenant/team/workload), `ciphertext`, `key_id`, and rotation metadata (`rotated_at`, `rotation_interval_days`, `next_rotation_at`). `secret_refs` records where a secret is injected (run, env var, file path) so access is auditable. Plaintext never persists; resolution happens at run launch inside the sandbox boundary. See D33.

### Workers

`workers` stores identity (`worker_id`, signed-token subject), `capabilities`, `status`, and last-seen. `worker_leases` holds the current run lease (`run_id`, `expires_at`, `attempt`); `worker_heartbeats` is a bounded rolling table. Lease expiry drives reassignment. See D34.

### Artifacts

`artifacts` records `run_id`, `location` (local path or `s3://`/`minio://` URI), `size_bytes`, `content_hash`, `retention_policy_id`, and `expires_at`. `retention_policies` are per-tenant. Rows are metadata only; bytes live in the configured backend. See D38.

### Cost Periods and Metering

`metering_events` is the append-only, high-volume usage fact (tenant, team, workload, run, model, tokens, tool calls, cost, `occurred_at`). `cost_periods` materializes day/week/month buckets per team and workload, including `cost_per_completed_task` and `roi_flag`. Raw events are retained briefly; periods are the durable query surface. See D35.

### Reconciliation / Desired State

`desired_specs` stores the declared fleet manifest set for a source (`git`/`api`), its `revision`, and content hash. `reconcile_state` stores last observed/desired hash and `last_reconcile_at` per source. `drift_records` records a field-level difference (`object_ref`, `field`, `desired_value`, `observed_value`, `resolution`). See D22.

### Tenant Scoping Rules

- Every fleet table has a non-null `tenant_id` (no global reference data today).
- Reads and writes are tenant-filtered at the store layer; cross-tenant queries are admin-only and audited.
- FKs are tenant-consistent via composite `(parent_id, tenant_id)`; a mismatched child raises `TenantScopeError`.
- Unique constraints are tenant-qualified (e.g. `(tenant_id, name)`), never global.
- A scoping matrix is reviewed at M25 exit; tenant-scoping tests are an acceptance criterion.

### Migration Strategy

- One Alembic series, forward-only; downgrades documented but unsupported.
- Backfill from v0.1.0: existing workloads/runs/certs get `default` tenant + team; new columns are nullable → backfilled → `NOT NULL`.
- Auto-migration on startup (preserved from v0.1.0): `alembic upgrade head` in the lifespan hook before serving; idempotent, safe to re-run.
- The model surface freezes at M25 exit; later changes require a new migration.

## Data Model

| Table | Key columns |
|-------|-------------|
| `tenants` / `teams` / `memberships` | `id`, `tenant_id`, `name`, `attribution_key`, `operator_id`, `role` |
| `triggers` | `id`, `tenant_id`, `source`, `filter`, `dedup_key_template`, `cooldown_seconds`, `rate_limit`, `task_template`, `admission_rule`, `target_ref`, `enabled` |
| `trigger_events` / `trigger_runs` | `id`, `trigger_id`, `dedup_key`, `outcome`, `run_id`, `status`, `reason` |
| `pipelines` / `pipeline_runs` | `id`, `tenant_id`, `version`, `nodes`, `edges`, `gates`, `budget`, `node_id`, `parent_run_id`, `child_run_id` |
| `policy_packs` / `policy_decisions` | `id`, `tenant_id`, `version`, `parent_pack_id`, `lint_status`, `content_hash`, `run_id`, `outcome`, `reason`, `originating_rule_id` |
| `secrets` / `secret_refs` | `id`, `tenant_id`, `name`, `scope`, `ciphertext`, `key_id`, `next_rotation_at`, `run_id`, `injection_target` |
| `workers` / `worker_leases` / `worker_heartbeats` | `worker_id`, `capabilities`, `status`, `run_id`, `expires_at`, `last_seen` |
| `artifacts` / `retention_policies` | `id`, `run_id`, `location`, `size_bytes`, `content_hash`, `expires_at` |
| `metering_events` / `cost_periods` | `id`, `tenant_id`, `team_id`, `period`, `cost`, `cost_per_completed_task`, `roi_flag` |
| `desired_specs` / `reconcile_state` / `drift_records` | `source_id`, `revision`, `object_ref`, `field`, `desired_value`, `observed_value`, `resolution` |

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Migration fails mid-backfill | Transactional per revision; control plane refuses to serve and logs the failing revision |
| Orphaned tenant scoping | Composite FKs + tenant-qualified uniques prevent cross-tenant linkage |
| Secret ciphertext read without key | Resolution fails closed; run not admitted; attempt audited |
| Cost period recompute race | Materialization idempotent, keyed by `(tenant, team, workload, period)` |
| Reconcile writes stale desired hash | Compare-and-set on `reconcile_state`; loser re-observes next tick |

## Security

Tenant isolation is the primary control: every query is tenant-filtered at the store layer, and composite FKs make cross-tenant rows structurally impossible. Secrets are encrypted at rest with a key-id reference; plaintext is resolved only at injection time and never enters agent context, logs, traces, or audit. Policy decision records are append-only and tamper-evident (DD-07). Worker identity columns back the signed-token/mTLS admission path (D34).

## Testing

- Model unit tests: valid definitions accepted; malformed trigger/pipeline/policy definitions rejected with actionable errors.
- Migration up/down test against a seeded v0.1.0 database; runs/certs/attestations survive intact.
- Tenant-scoping matrix: each table proves reads/writes cannot cross tenants; composite-FK violation raises.
- Backfill test: v0.1.0 rows receive `default` tenant/team and remain queryable.
- Coverage > 95% for new model and migration modules.

## Open Questions

- Should `metering_events` be partitioned by time or moved to a time-series store?
- Are policy packs tenant-global or team-scoped by default when both are specified?
- Does `default` tenant removal become possible post-v0.2.0, or stay permanent?
- Should artifact `location` be provider-abstracted now to ease a later store swap?

## See Also

- [PRD 05: Features](../prd/05-features.md) — Workload Model & Registry; Fleet Control & Scheduling
- [PRD 09: Roadmap](../prd/09-roadmap.md) — pillars H, J, K, L, M
- [WBS v0.2.0 Part 1](../wbs/v0.2.0/wbs-v0.2.0-part1-foundation.md) — M25
- [State Store Design](state-store-design.md) (D7) — the v0.1.0 schema this extends
- [Reconciliation Design](reconciliation-design.md) (D22) — desired-state semantics
- [Trigger Service v2 Design](trigger-service-v2-design.md) (D23) — trigger semantics
- [Secrets & RBAC Design](secrets-rbac-design.md) (D33) · [Fleet Execution Design](fleet-execution-design.md) (D34) · [Cost & ROI v2 Design](cost-roi-v2-design.md) (D35) · [Reporting, Tenancy & Distribution Design](reporting-tenancy-distribution-design.md) (D38)
- [Design Decisions](design-decisions.md) — DD-05, DD-07
