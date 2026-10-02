# D38: Reporting, Tenancy & Distribution Design

> Status: implemented for M57 (digest, audit export, evidence packs, retention/PII,
> tenant purge), M58 (multi-tenant isolation), and M59 (distribution & supply
> chain); M60 (replay) shipped — see [Operator Experience (D36)](operator-experience-design.md)

**Milestones:** M57–M59, M60 · **Extends:** D7

## Problem

The plane produces audit, cost, cert, and run data but does not report on itself, cannot meet retention/compliance obligations, is not genuinely multi-tenant, and has no supported way to reach a real cluster. v0.2.0 closes all four: a weekly digest, audit export with an integrity proof, compliance evidence packs, per-tenant retention with PII scrubbing and verifiable purge, hard tenant isolation, and a hardened distribution/supply chain (Helm, backup/restore, air-gapped, signed releases).

## Overview

```
 audit/attest/cost/health ──▶ reporting ──▶ digest · evidence pack · exports
        │
        ├── retention/PII/purge ──▶ tenant data lifecycle + purge certificate
        │
 tenants/teams ──▶ isolation boundary ──▶ every store tenant-scoped (403/404)
        │
        └── distribution ──▶ Helm · k3d/kind · backup/restore · air-gap
                             Homebrew/PyPI · SBOM/cosign/provenance
                             demo profile · federation (flag)
```

## Design

### Weekly fleet digest

A scheduled job renders a digest: spend (period, by team/agent, CPCT/ROI), drift and quarantine events, approvals (volume, latency, bottlenecks), top/bottom ROI agents, and a health summary. Output is markdown and is delivered via Slack/email per team routing (D36 prefs). Scheduling is per-tenant; content is tenant-scoped and links back to live views.

### Audit export

Export the tamper-evident audit log (D8/D26) as CSV or JSON for a period/tenant. Each export carries an integrity proof — a Merkle root over the exported range plus the chain head — so a third party can verify the export matches the log. Exports are themselves audited.

### Compliance evidence pack

For a period, assemble approvals (who/why/when), attestation history (certs, profiles, model identity, signatures), and spend (the metering ledger). The pack is a signed, self-contained bundle (index + files + manifest) that an auditor can verify offline.

### Data retention

Per-tenant retention policies across runs, logs, artifacts, audit, and metering. Policies set `retain_days` per data class; a scheduled purge deletes expired data and leaves audit evidence. Artifacts (D36) use the same policy engine. Retention never deletes data under legal hold.

**Audit retention is best-effort global-prefix pruning.** The audit chain is a single global hash chain, so retention can only remove a contiguous *leading prefix* under the pruning anchor (D38): pruning stops at the first record that is protected — one whose tenant has no expired audit policy, is under legal hold, or is still within its retention window. Two consequences follow, and they are deliberate rather than bugs:

- If the leading global record is retained (for example, it belongs to a tenant with no expired policy), pruning stops immediately and **no** records are deleted, even when a later record's tenant *has* expired data. The purge still succeeds with `deleted=0`; `deleted=0` is the honest indicator that nothing was eligible at the anchor.
- When pruning does proceed it removes the leading prefix as a whole. Because tenants interleave in the global chain, records from other tenants inside that prefix that are themselves expired are removed too — audit retention is global-prefix scoped, not strictly per-tenant inside the prefix.

Records after the stop point are retained regardless of their own tenant's policy. Per-tenant audit checkpoints (so each tenant's expired audit can be pruned independently without breaking the chain) are future work. The behavior is pinned by `tests/test_reporting_retention.py`.

### PII scrubbing

Configurable PII detection/redaction in logs, traces, and stored artifacts before they persist. Ordering versus the audit hash chain is explicit: **scrub before hashing** for fields that are part of the hashed record, so the chain stays verifiable without storing raw PII. For audit events whose integrity depends on raw content, store a salted hash of the redacted field and redact the value. The ordering rule is documented and tested — scrubbing must never corrupt the chain.

### Tenant purge (right-to-delete)

Scheduled or on-demand purge removes all of a tenant's data across every store, coordinates artifact deletion (including S3/MinIO), and emits a **purge certificate**: a signed record of scope, stores, row/object counts, timestamp, and verifier. A partial purge is a failure and is retried; the certificate asserts completeness.

The global tamper-evident audit log is **not** deleted by a tenant purge: it is the plane's integrity root, and removing a tenant's slice would break the hash chain for every other tenant. The purge records it as a retained store (`audit`, zero deleted) so the certificate is explicit about the exclusion, and the `tenant.purged` event itself is appended to the chain.

### Multi-tenant isolation

Every store is tenant-scoped (D21): runs, certs, triggers, pipelines, secrets, artifacts, and cost. Per-tenant budgets/spend caps (D35), policy packs (D29), and API keys/roles (D33) are isolated and enforced. Cross-tenant access is denied on every API/CLI/UI path with consistent `403`/`404` and no existence leak. Tenant admin can create/suspend/quota tenants; a suspended tenant cannot submit runs or receive deliveries. An adversarial isolation suite is built first, and any leak is a release blocker.

**Decisions (M58):**

- **Auth-bound header.** `X-Hiveplane-Tenant` selects the tenant, never an identity. When `settings.auth.enabled`, the resolved principal's tenant must equal the header tenant (a system principal may select any tenant), and the acting role comes from the principal/membership. When auth is disabled the header remains trusted local plumbing, so demo/field flows are unchanged.
- **Consistent denial.** `TenantScopeError` → `403`; `TenantNotFoundError`/`TeamNotFoundError` → `404`; a foreign read is indistinguishable from an absent resource.
- **Suspension.** `Tenant` carries `status` (`ACTIVE`/`SUSPENDED`) and a `quota`; `TenantAdminService.require_active` gates run submission and delivery, and the auth service refuses a suspended tenant's keys. Cross-tenant lifecycle mutations require a system context; a tenant admin may suspend/reinstate/quota its own tenant.
- **Per-tenant budgets.** Run usage is priced and accrued under the run's tenant in the `budget` store and bridged into the M49/M50 `CostService`, so period budgets, spend caps, showback, and ROI are live per tenant; an enforced tenant cap fails admission closed.
- **Per-tenant policy packs.** Packs are keyed `(tenant_id, name)` in memory and evaluated against the run's tenant, with a per-tenant default pack and team-pack precedence. `PolicyContext` carries the tenant. Postgres persistence of packs remains deferred.
- **Global exceptions (deliberately not tenant-partitioned):** the tamper-evident audit chain, transparency log, tool kill switch, incident mode, and HA leader lease. Tenant purge records the audit store as retained rather than deleting a slice of the shared chain.

### Distribution

- **Helm chart** for the full stack (API, UI, Postgres, Redis, telemetry) with a `values.schema.json`; the chart is versioned and documented as a public contract.
- **k3d/kind reference deploy** verified end-to-end (CI or a documented runbook).
- **Backup/restore** of control-plane state with integrity checks (export → restore → verify), covering migrations and DR.
- **Air-gapped install bundle**: offline images plus seed data; installs with no internet access.
- **Homebrew tap + PyPI hardening** (`brew install hiveplane`; pinned/signed wheels).
- **Release supply chain**: SBOM, cosign-signed images, and SLSA-style provenance, reproducible in CI.
- **Demo profile**: pre-baked datasets plus seeded failure scenarios for screenshots and talks.
- **Federation (stretch, behind a flag)**: register remote planes and aggregate a fleet view; disabled by default.

## Decisions

The following mechanics are settled by the M57 implementation. Acceptance-level
behaviors are exercised end-to-end by `tests/test_reporting_acceptance.py`; the
pruning anchor and audit-chain pruning are covered by
`tests/test_reporting_retention.py`.

- **Global audit chain + pruning anchor.** The tamper-evident audit log stays a
  single global hash chain. Retention prunes only a *leading prefix* and advances
  a persisted pruning anchor to the hash of the last deleted record, so `verify()`
  still holds for every retained record. A tenant's records are never excised from
  the middle of the chain.
- **Scrub-before-hash.** PII in audit `detail` is redacted *before* the record
  hash is computed, so no raw PII is persisted and the chain verifies over the
  redacted content. Artifact text is scrubbed on capture for the same reason. The
  `PIIScrubber` is enabled by `HIVEPLANE_REPORTING__PII_ENABLED`.
- **Deterministic retention policy ids.** A policy is keyed
  `retention:{tenant_id}:{data_class}` (truncated to 64 chars), making upserts
  idempotent and tenant-qualified.
- **Audit is retained by tenant purge.** Tenant purge deletes the mutable,
  tenant-scoped stores only. The global audit log is deliberately **excluded**
  (recorded as store `audit` with `deleted=0`) because deleting one tenant's rows
  would break the chain for every other tenant; the `tenant.purged` event itself
  is appended to the chain.
- **M57 purge scope.** runs; artifacts (blobs + metadata); metering (events,
  periods, alerts, dead letters); logs (auth keys/logins/access); delivery
  (attempts/decisions/preferences); tenancy (tenant/teams/memberships); and
  reporting reports/exports/evidence. The purge ledger itself is **not** deleted —
  it is the certificate of record. Tenant-scoped tables that are adjacent to runs
  but owned by other subsystems — `eval_samples`/`judge_scores`, `run_feedback`,
  `shadow_runs`/`canary_samples`/`canary_rollouts`/`experiment_*`, and
  `reconcile_runs` — are **retained** and recorded as explicit
  `deleted=0` entries (`eval`, `feedback`, `progressive`, `reconcile`) so the
  certificate does not claim completeness it does not have; their purge moves to
  M58 full isolation along with certs/triggers/pipelines/secrets. The certificate
  lists the exact wired target set, retained entries included.
- **Per-tenant / per-period Merkle proof (unsigned).** Audit export proves a
  tenant-and-period *slice* of the global chain — a Merkle root over the slice's
  leaf hashes plus the slice chain head and leaf count — not the whole global
  chain, so an export never exposes another tenant's records. Every exported
  record must be self-consistent (`hash == AuditChain.compute_hash(prev_hash,
  record)`); because tenants interleave in the global chain, slice-local
  adjacency is **not** required. The export carries an **unsigned**
  Merkle-root/chain-head proof: only evidence packs and purge certificates are
  Ed25519-signed (domain-separated payloads `hiveplane/reporting/evidence/v1` and
  `hiveplane/reporting/purge/v1`).

## Data Model

| Table | Key columns |
|-------|-------------|
| `retention_policies` | `id`, `tenant_id`, `data_class`, `retain_days`, `legal_hold` |
| `purge_records` | `id`, `tenant_id`, `scope`, `counts`, `certificate`, `completed_at` |
| `report_runs` | `id`, `tenant_id`, `kind`, `period`, `output_ref`, `generated_at` |
| `evidence_packs` | `id`, `tenant_id`, `period`, `manifest`, `signature`, `created_at` |
| `audit_exports` | `id`, `tenant_id`, `range`, `format`, `merkle_root`, `created_at` |
| `backups` | `id`, `created_at`, `integrity_hash`, `restored_at` |
| `remote_planes` | `id`, `endpoint`, `status`, `last_seen` (federation) |

## Interfaces/API

```
GET  /reports/digest?period=...                 → markdown / Slack / email
GET  /audit/export?period=...&format=csv|json    → export + integrity proof
POST /compliance/evidence-pack { period }        → signed bundle
GET  /retention/policies  ·  PUT /retention/policies/{class}
POST /tenants/{id}/purge                         → purge certificate
POST /admin/tenants  ·  POST /admin/tenants/{id}/suspend
POST /backup  ·  POST /restore?verify=true
```

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Partial purge | Retried; no certificate issued until complete |
| PII scrub corrupts chain | Scrub-before-hash ordering enforced; verification fails the build |
| Cross-tenant leak | Release blocker; the adversarial suite must pass |
| Suspended tenant submits | Denied at admission; deliveries suppressed |
| Restore integrity mismatch | Restore refused; the backup is flagged corrupt |
| Air-gapped install needs network | Bundle is self-contained; install asserts offline |

## Security

Tenant isolation is the primary control, enforced at the store layer with composite FKs (D21) and consistent 403/404 denials. Purge is irreversible and audited with a signed certificate. PII scrubbing preserves audit verifiability by scrubbing before hashing. Release artifacts are cosign-signed and carry SBOM/provenance; the air-gapped bundle is signature-verified before install. Federation is off by default and explicitly flagged.

## Testing

- Digest content is correct for seeded spend/drift/approvals/ROI; routing honors prefs.
- Audit export round-trips and its Merkle/integrity proof verifies.
- The evidence pack contains approvals + attestations + spend and verifies offline.
- Retention purge deletes on schedule and leaves a purge certificate; legal hold blocks deletion.
- PII is scrubbed from logs/artifacts and the audit chain still verifies.
- Adversarial isolation suite: cross-tenant read/write all denied; suspended tenant denied.
- Helm lints and templates render; k3d smoke test; backup/restore round-trip; cosign/SBOM/provenance verify.

## Open Questions

- Do retention policies default to a global floor when a tenant sets none?
- Should purge wait for in-flight runs/artifacts to settle before certifying?
- Is federation single-sign-on across planes, or read-only aggregation only?

The audit-proof shape is settled: exports prove a per-tenant/per-period slice
rather than the whole global chain (see [Decisions](#decisions)).

## See Also

- [PRD 05: Features](../prd/05-features.md) — Reporting & Compliance, Secrets & Identity
- [PRD 09: Roadmap](../prd/09-roadmap.md) — pillars L, P
- [WBS v0.2.0 Part 17](../wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md) — M57–M58
- [WBS v0.2.0 Part 18](../wbs/v0.2.0/wbs-v0.2.0-part18-distribution-replay.md) — M59
- [State Store Design](state-store-design.md) (D7)
- [Fleet Control Data Model Design](fleet-control-data-model-design.md) (D21)
- [Secrets & RBAC Design](secrets-rbac-design.md) (D33)
- [Cost, Showback & ROI v2 Design](cost-roi-v2-design.md) (D35)
- [Operator Experience Design](operator-experience-design.md) (D36)
- [Platform API & Extensibility Design](platform-api-design.md) (D37)
- [Design Decisions](design-decisions.md) — DD-05, DD-07
