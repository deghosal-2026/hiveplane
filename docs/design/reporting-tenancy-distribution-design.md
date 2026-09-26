# D38: Reporting, Tenancy & Distribution Design

> Status: draft

**Milestones:** M57–M59 · **Extends:** D7

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

### PII scrubbing

Configurable PII detection/redaction in logs, traces, and stored artifacts before they persist. Ordering versus the audit hash chain is explicit: **scrub before hashing** for fields that are part of the hashed record, so the chain stays verifiable without storing raw PII. For audit events whose integrity depends on raw content, store a salted hash of the redacted field and redact the value. The ordering rule is documented and tested — scrubbing must never corrupt the chain.

### Tenant purge (right-to-delete)

Scheduled or on-demand purge removes all of a tenant's data across every store, coordinates artifact deletion (including S3/MinIO), and emits a **purge certificate**: a signed record of scope, stores, row/object counts, timestamp, and verifier. A partial purge is a failure and is retried; the certificate asserts completeness.

### Multi-tenant isolation

Every store is tenant-scoped (D21): runs, certs, triggers, pipelines, secrets, artifacts, and cost. Per-tenant budgets/spend caps (D35), policy packs (D29), and API keys/roles (D33) are isolated and enforced. Cross-tenant access is denied on every API/CLI/UI path with consistent `403`/`404` and no existence leak. Tenant admin can create/suspend/quota tenants; a suspended tenant cannot submit runs or receive deliveries. An adversarial isolation suite is built first, and any leak is a release blocker.

### Distribution

- **Helm chart** for the full stack (API, UI, Postgres, Redis, telemetry) with a `values.schema.json`; the chart is versioned and documented as a public contract.
- **k3d/kind reference deploy** verified end-to-end (CI or a documented runbook).
- **Backup/restore** of control-plane state with integrity checks (export → restore → verify), covering migrations and DR.
- **Air-gapped install bundle**: offline images plus seed data; installs with no internet access.
- **Homebrew tap + PyPI hardening** (`brew install hiveplane`; pinned/signed wheels).
- **Release supply chain**: SBOM, cosign-signed images, and SLSA-style provenance, reproducible in CI.
- **Demo profile**: pre-baked datasets plus seeded failure scenarios for screenshots and talks.
- **Federation (stretch, behind a flag)**: register remote planes and aggregate a fleet view; disabled by default.

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

- Is the audit Merkle root per-tenant, or global with tenant leaves?
- Do retention policies default to a global floor when a tenant sets none?
- Should purge wait for in-flight runs/artifacts to settle before certifying?
- Is federation single-sign-on across planes, or read-only aggregation only?

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
