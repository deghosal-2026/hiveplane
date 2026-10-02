# M57 — Reporting, Compliance & Data Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship M57 of v0.2.0 — a weekly fleet digest, audit export with an integrity proof, a compliance evidence pack, per-tenant retention with PII scrubbing and verifiable tenant purge.

**Source of truth:** [`docs/design/reporting-tenancy-distribution-design.md`](../design/reporting-tenancy-distribution-design.md) (D38), [`docs/wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md`](../wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md) (M57 items #440–#448), PRD 05 "Reporting & Compliance".

**Architecture:** A new `hiveplane.reporting` package owns digest generation, audit export, evidence packs, retention, PII scrubbing, and tenant purge. It composes existing stores/services (`CostService`, `DriftStore`, `ApprovalService`, `CertificationStore`, `ArtifactService`, `AuditLog`) rather than duplicating them. New durable state lives in four `_TenantScoped` tables (`report_runs`, `report_schedules`, `audit_exports`, `evidence_packs`, `purge_records`) added by forward-only Alembic revision `0031`. A `notification_preferences` table (revision `0032`) makes M51 prefs durable and target-bearing so digests can route per team. Audit retention uses a **pruning anchor**: the hash chain stays verifiable after an expired prefix is removed.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0, Alembic, FastAPI, cryptography (Ed25519), pytest, ruff, mypy strict.

## Global Constraints

- **Tenant scoping:** every new store/service method takes `ctx: TenantContext = DEFAULT_CONTEXT` keyword-only (the M25-01 documented deviation). Reads filter by tenant (`ctx.scopes`); cross-tenant writes raise `TenantScopeError`. `SYSTEM_CONTEXT` bypasses.
- **Direction:** forward-only. No backward-compatibility shims. No comments unless the surrounding file already uses them.
- **Pydantic:** `model_config = ConfigDict(extra="forbid")`; ids `Field(min_length=1, max_length=64)`; operator/name/detail strings `max_length=253`; artifact-like refs `max_length=512`.
- **Persistence:** new ORM rows subclass `_TenantScoped` and store the exact pydantic record in `payload: Mapped[dict]` (JSONB). New tables must be added to the expected set in `tests/test_persistence_schema.py` and (unless global) to the tenant-scoped list.
- **API:** routers use `Annotated` deps from `api/deps.py`; reads gate on `Permission.FLEET_READ`; writes gate on `Permission.REPORTS_MANAGE`. Domain errors are mapped to HTTP statuses (403/404/409) via the app error tables or inline `HTTPException`.
- **Reuse:** use `hiveplane.fleet.artifacts.RetentionPolicy` for retention policies; use `hiveplane.persistence.audit.AuditChain` math; reuse Ed25519 signing primitives (see `hiveplane.artifacts.export` and `hiveplane.certification.signing`).
- **PII ordering:** scrub **before** hashing for any field that is part of the audit record, so the chain stays verifiable without storing raw PII.
- **Tests:** module docstrings carry the work-item id (`(M57-0x)`). In-memory fakes by default; `pg_engine` + `tests/postgres.py` helpers for DB paths. No new pytest markers.
- **Exit gate (every task boundary):** `python -m pytest` green; `python -m ruff check` clean; `python -m mypy src/ tests/` clean; new-module coverage >95%.

## File Structure

**Create**
- `src/hiveplane/reporting/__init__.py` — public exports + `__all__`.
- `src/hiveplane/reporting/errors.py` — `ReportingError`, `ReportNotFoundError`, `AuditExportNotFoundError`, `EvidencePackNotFoundError`, `PurgeIncompleteError`.
- `src/hiveplane/reporting/models.py` — all M57 pydantic models (Task 1).
- `src/hiveplane/reporting/store.py` — `ReportingStore` protocol, `InMemoryReportingStore`, `PostgresReportingStore`, `build_reporting_store`.
- `src/hiveplane/reporting/factory.py` — `build_reporting_store` re-export + service builders added per task.
- `src/hiveplane/reporting/merkle.py` — Merkle root/proof (Task 4).
- `src/hiveplane/reporting/digest.py` — `DigestService` (Task 2).
- `src/hiveplane/reporting/scheduler.py` — `DigestScheduler` (Task 3).
- `src/hiveplane/reporting/audit_export.py` — `AuditExportService` (Task 4).
- `src/hiveplane/reporting/evidence.py` — `EvidencePackService` (Task 5).
- `src/hiveplane/reporting/retention.py` — `RetentionEnforcer` (Task 6).
- `src/hiveplane/reporting/pii.py` — `PIIScrubber` (Task 7).
- `src/hiveplane/reporting/purge.py` — `TenantPurgeService` (Task 8).
- `src/hiveplane/api/reporting.py` — reporting/compliance/retention/purge router.
- `src/hiveplane/persistence/migrations/versions/0031_reporting.py`
- `src/hiveplane/persistence/migrations/versions/0032_notification_preferences.py`
- Tests: `tests/test_reporting_models.py`, `tests/test_reporting_store.py`, `tests/test_reporting_digest.py`, `tests/test_reporting_scheduler.py`, `tests/test_reporting_merkle.py`, `tests/test_reporting_audit_export.py`, `tests/test_reporting_evidence.py`, `tests/test_reporting_retention.py`, `tests/test_reporting_pii.py`, `tests/test_reporting_purge.py`, `tests/test_api_reporting.py`, `tests/test_persistence_migration_0031.py`, `tests/test_persistence_migration_0032.py`.

**Modify**
- `src/hiveplane/persistence/models.py` — new rows + `audit_anchor` (Task 6).
- `src/hiveplane/config.py` — `ReportingSettings` + `Settings.reporting`.
- `src/hiveplane/auth/models.py`, `src/hiveplane/auth/rbac.py` — `Permission.REPORTS_MANAGE`, `Scope.REPORTS_WRITE`.
- `src/hiveplane/api/deps.py`, `src/hiveplane/api/app.py` — service state, deps, router include.
- `src/hiveplane/persistence/audit.py`, `src/hiveplane/persistence/postgres_audit.py` — anchor-aware verify + `prune` (Task 6).
- `src/hiveplane/delivery/models.py`, `src/hiveplane/delivery/store.py` — target-bearing, persisted prefs (Task 3).
- `src/hiveplane/artifacts/service.py` — PII scrub hook on capture (Task 7).
- `docs/design/reporting-tenancy-distribution-design.md` (status + decisions), `CHANGELOG.md`, `docs/USER_GUIDE.md`.
- `tests/test_persistence_schema.py`, `tests/test_tenant_scoping_isolation.py` (new stores/tables).

---

## Phase 1 — Foundation

### Task 1: Reporting package foundation (models, store, persistence, wiring)

**Files:**
- Create: `src/hiveplane/reporting/__init__.py`, `errors.py`, `models.py`, `store.py`, `factory.py`, `src/hiveplane/api/reporting.py`
- Create: `src/hiveplane/persistence/migrations/versions/0031_reporting.py`
- Modify: `src/hiveplane/persistence/models.py`, `src/hiveplane/config.py`, `src/hiveplane/auth/models.py`, `src/hiveplane/auth/rbac.py`, `src/hiveplane/api/deps.py`, `src/hiveplane/api/app.py`, `tests/test_persistence_schema.py`
- Test: `tests/test_reporting_models.py`, `tests/test_reporting_store.py`, `tests/test_persistence_migration_0031.py`

**Interfaces (exact — later tasks depend on these):**
- `ReportKind`: `DIGEST|EVIDENCE|AUDIT_EXPORT|RETENTION|PURGE`.
- `ReportRun(report_id, tenant_id, kind: ReportKind, period_key, output_ref, generated_at)`.
- `ReportSchedule(schedule_id, tenant_id, team_id: str|None, cron, period_kind: CostPeriodKind=WEEK, channels: list[DeliveryChannel], active: bool=True, next_run: AwareDatetime|None, created_at)`.
- `AuditExportFormat`: `CSV|JSON`.
- `IntegrityProof(algorithm="sha256", merkle_root(64), leaf_count>=0, first_sequence: int|None, last_sequence: int|None, chain_head(64))`.
- `AuditExport(export_id, tenant_id, period_start, period_end, format, record_count>=0, integrity_proof, content, created_at)`.
- `EvidenceFile(name, media_type, digest, content)`; `PackSignature(key_id, algorithm="ed25519", digest, signature)`.
- `EvidencePack(pack_id, tenant_id, period_start, period_end, files: list[EvidenceFile], signature: PackSignature, created_at)`.
- `RetentionDataClass`: `RUNS|LOGS|ARTIFACTS|AUDIT|METERING`.
- `StorePurgeCount(store, deleted>=0)`; `PurgeCertificate(purge_id, tenant_id, scope, stores: list[StorePurgeCount], total_deleted>=0, completed_at, verifier, signature: PackSignature)`; `PurgeRecord(purge_id, tenant_id, scope, certificate, completed_at)`.
- `Redaction(kind, count>=0)`; `ScrubResult(text, redactions: list[Redaction])` (Task 7 model; define here).
- `ReportingStore` protocol methods (all ctx-keyword): `save_report`, `get_report`, `list_reports(*, tenant_id, kind=None)`, `save_schedule`, `get_schedule`, `list_schedules(*, tenant_id)`, `save_export`, `get_export`, `list_exports(*, tenant_id)`, `save_evidence`, `get_evidence`, `list_evidence(*, tenant_id)`, `save_purge`, `get_purge`, `list_purges(*, tenant_id)`, `clear`.
- `build_reporting_store(settings=None) -> ReportingStore` (postgres when `settings.execution.store == "postgres"`).
- `ReportingSettings`: `enabled: bool=False`, `digest_cron: str="0 8 * * 1"`, `digest_period: CostPeriodKind=WEEK`, `pii_enabled: bool=False`, `pii_salt: str="hiveplane-pii"`, `default_retain_days: int=90`, `signing_key_id: str="reporting"`.
- New RBAC: `Permission.REPORTS_MANAGE="reports_manage"` (ADMIN), `Scope.REPORTS_WRITE` → `REPORTS_MANAGE`.
- App: `app.state.reporting_store`; deps `get_reporting_store(request)`; router include.
- Endpoints: `GET /reports` → `list[ReportRun]` (FLEET_READ); `GET /reports/{report_id}` → `ReportRun` (404).

**Steps:**
1. Write failing `tests/test_reporting_models.py`: valid construction + `extra="forbid"` rejection + non-empty id validation for `ReportRun`, `AuditExport`/`IntegrityProof`, `EvidencePack`, `PurgeRecord`.
2. Implement `errors.py`, `models.py`, `__init__.py` until those pass.
3. Write failing `tests/test_reporting_store.py`: seed each record type, assert tenant read visibility, cross-tenant invisibility (read returns `None`/omits), `clear()`, deterministic ordering; then a `pg_engine` section using `reset_database`/`ensure_schema` mirroring `tests/test_tenancy_store.py`.
4. Implement `store.py` + `factory.py`.
5. Add ORM rows `ReportRunRow`, `ReportScheduleRow`, `AuditExportRow`, `EvidencePackRow`, `PurgeRecordRow` to `persistence/models.py` (PK = the record id, `tenant_id String(64) index`, `payload` JSONB). Add `0031_reporting.py` creating them idempotently (`Base.metadata.tables[name].create(bind, checkfirst=True)`), `down_revision="0030"`.
6. Write failing `tests/test_persistence_migration_0031.py` (upgrade/downgrade/upgrade + table presence), mirroring `tests/test_persistence_migrations.py`.
7. Add `ReportingSettings` to `config.py` and `reporting: ReportingSettings` to `Settings`.
8. Add permission/scope + RBAC mappings; assert in `tests/test_reporting_models.py` or a small RBAC test that ADMIN has `REPORTS_MANAGE`.
9. Wire `app.state.reporting_store = build_reporting_store(settings)` and `get_reporting_store`; add `api/reporting.py` with the two read endpoints and register the router in `create_app`'s include block. Add a `client` test proving `GET /reports` is tenant-scoped via `X-Hiveplane-Tenant`.
10. Update `tests/test_persistence_schema.py` expected-table set and `tests/test_tenant_scoping_isolation.py` if it enumerates stores.
11. Run the exit gate.

**Acceptance:** All four record families persist and round-trip in memory and (when available) Postgres; tables exist and migrate; RBAC has the new permission; `GET /reports` is tenant-scoped.

---

### Task 2: Weekly fleet digest (M57-01)

**Files:**
- Create: `src/hiveplane/reporting/digest.py`
- Modify: `src/hiveplane/reporting/models.py` (add `DigestContent`, `DriftDigest`), `src/hiveplane/reporting/factory.py`, `src/hiveplane/api/reporting.py`, `src/hiveplane/api/app.py` (state/deps), `src/hiveplane/reporting/__init__.py`
- Test: `tests/test_reporting_digest.py`, `tests/test_api_reporting.py`

**Interfaces:**
- `DriftDigest(quarantined: int, reinstated: int, active: int, drifting: list[str])`.
- `DigestContent(tenant_id, period_key, period_start: date, period_end: date, generated_at: AwareDatetime, spend: ShowbackReport, roi: RoiReport, drift: DriftDigest, approvals: ApprovalReport)`.
- `DigestService(reporting_store, cost_service, drift_store, approval_service, registry_service, *, clock=None)` with `bind_audit(audit)`.
- `build(tenant_id, *, kind=CostPeriodKind.WEEK, at=None) -> DigestContent` — spend via `cost_service.showback(group_by="team")`, ROI via `cost_service.roi(...)`, approvals via `approval_service.list(ctx=...)` + `health.analytics.approval_analytics`, drift via `drift_store.list_quarantines(ctx=...)` (count `QuarantineStatus.ACTIVE` and `reinstated_at` in period) plus per-workload `drift_store.list_assessments` over `registry_service.list_workloads(ctx=...)` to collect `verdict == DRIFTED` workloads.
- `render_markdown(content) -> str` — sections: spend, ROI (top 3 / bottom 3 by `roi`), drift, approvals (volume/latency/bottleneck).
- `generate(tenant_id, *, kind=WEEK, at=None, actor="system") -> tuple[DigestContent, ReportRun]` — persists a `ReportRun(kind=DIGEST, period_key=...)`, audits `"report.digest.generated"`.
- API `GET /reports/digest?period=week&format=json|markdown` (FLEET_READ) → `DigestContent` or markdown string.

**Steps:**
1. Failing `tests/test_reporting_digest.py`: build seeded in-memory `CostService`/`DriftStore`/`ApprovalService`/`RegistryService` and assert `DigestContent` spend/ROI/drift/approval values and markdown contains each section; `generate` writes a `ReportRun` and an audit entry.
2. Implement `digest.py`; export models.
3. Failing `tests/test_api_reporting.py`: `GET /reports/digest` returns JSON for the acting tenant and markdown when `format=markdown`; a second tenant sees only its own data (`X-Hiveplane-Tenant`).
4. Wire service on `app.state`, add dep `get_digest_service`, add endpoint.
5. Run exit gate.

**Acceptance:** Digest content is accurate for seeded spend/drift/approvals/ROI; markdown renders all four sections; generation is persisted and audited; endpoint is tenant-scoped.

---

### Task 3: Digest scheduling & per-team routing (M57-02)

**Files:**
- Create: `src/hiveplane/reporting/scheduler.py`
- Modify: `src/hiveplane/delivery/models.py` (add `DeliveryDestination` targets to `NotificationPreference`), `src/hiveplane/delivery/store.py` (`save_preference`/`get_preference`/`list_preferences`/`clear`), `src/hiveplane/delivery/service.py` (persist + route prefs), `src/hiveplane/persistence/models.py` + `0032_notification_preferences.py`, `src/hiveplane/api/reporting.py`, `src/hiveplane/api/app.py`
- Test: `tests/test_reporting_scheduler.py`, `tests/test_persistence_migration_0032.py`, additions to `tests/test_api_reporting.py`

**Interfaces:**
- `NotificationPreference` gains `destinations: list[DeliveryDestination] = []` (`DeliveryDestination(channel: DeliveryChannel, target: str)` — check the existing delivery API; if a compatible type already exists, reuse it).
- `DeliveryStore` gains preference persistence; `DeliveryService` loads/saves prefs through the store and a team's `destinations` are used when no explicit destinations are passed to `deliver`.
- `DigestScheduler(reporting_store, digest_service, delivery_service, *, clock=None)`:
  - `schedule(tenant_id, *, team_id=None, cron, period_kind=WEEK, channels, ctx=DEFAULT_CONTEXT) -> ReportSchedule`
  - `due(*, at=None, ctx=DEFAULT_CONTEXT) -> list[ReportSchedule]` (uses `triggers.cron.CronExpression`)
  - `run_due(*, at=None, ctx=DEFAULT_CONTEXT) -> list[DeliveryAttempt]` — render markdown, deliver a `DeliveryEnvelope(event_type=COMPLETED, team_id=..., summary=<markdown>)`, advance `next_run`, audit `"report.digest.delivered"`.
- API: `POST /reports/digest/send` (team, period) and `POST /reports/schedules`, `GET /reports/schedules` (REPORTS_MANAGE / FLEET_READ).

**Steps:**
1. Failing `tests/test_reporting_scheduler.py`: schedule creation persists and computes `next_run`; `due` returns only matured schedules; `run_due` sends via a fake `MessageSender`, advances `next_run`, and audits.
2. Add `notification_preferences` table + migration `0032` (`down_revision="0031"`), persist prefs in the delivery store (in-memory + Postgres), and route to per-team destinations; test round-trip + migration.
3. Add endpoints + wiring; assert delivery honors a team preference (suppressed channel / quiet hours already handled by `DeliveryService._classify`).
4. Run exit gate.

**Acceptance:** Digest scheduling survives reload (persisted), fires when due, and routes per team using M51 preferences with persisted destinations.

---

## Phase 2 — Compliance & lifecycle

### Task 4: Audit export with integrity proof (M57-03)

**Files:**
- Create: `src/hiveplane/reporting/merkle.py`, `src/hiveplane/reporting/audit_export.py`
- Modify: `src/hiveplane/api/reporting.py`, `src/hiveplane/api/deps.py` (add `get_audit_log`), `src/hiveplane/api/app.py`, `src/hiveplane/reporting/__init__.py`
- Test: `tests/test_reporting_merkle.py`, `tests/test_reporting_audit_export.py`, additions to `tests/test_api_reporting.py`

**Interfaces:**
- `leaf_hash(value: str) -> str` (sha256 hex), `merkle_root(leaves: list[str]) -> str` (empty list → 64 zeros), `merkle_proof(leaves, index) -> list[str]`, `verify_proof(leaf, proof, root) -> bool`.
- `AuditExportService(reporting_store, audit_log, *, clock=None)`:
  - `export(ctx, *, period_start: AwareDatetime, period_end: AwareDatetime, format: AuditExportFormat) -> AuditExport` — filter `audit_log.records(ctx=ctx)` by `period_start <= created_at <= period_end`. **The audit hash chain is global, so a tenant/period slice is generally NOT contiguous (other tenants interleave).** Integrity for a slice is therefore **per-record self-consistency**: every exported record must satisfy `record.hash == AuditChain.compute_hash(record.prev_hash, record)` (proving the chain produced it); raise `ReportingError` otherwise. Do NOT require slice-local adjacency. Compute `IntegrityProof(merkle_root over record.hashes, leaf_count, first/last sequence, chain_head=records[-1].hash)`; content = CSV/JSON rows. (Corrections 2026-09-27: first replaced whole-chain verify; then removed slice-local adjacency, which wrongly rejected interleaved tenant windows.)
  - `verify(export: AuditExport) -> bool` — recompute the Merkle root from `export.content`, verify `chain_head` and `leaf_count`, and verify per-record self-consistency (each record's hash recomputes from its own `prev_hash` + content).
  - `list(ctx) -> list[AuditExport]`.
- API `GET /audit/export?period_start&period_end&format=csv|json` (**REPORTS_MANAGE** — it persists an export and audits, so it must not sit behind FLEET_READ) and `GET /audit/exports` / `GET /audit/exports/{export_id}` (FLEET_READ).

**Steps:**
1. Failing `tests/test_reporting_merkle.py`: known leaf sets produce stable roots; proof verifies for each index; tampered leaf fails; empty set.
2. Failing `tests/test_reporting_audit_export.py`: seed an `InMemoryAuditLog`, export CSV and JSON, assert record count, proof verifies, tampered export fails, cross-tenant export excludes other tenants' records, export is persisted + audited.
3. Implement `merkle.py`, `audit_export.py`; wire service, `get_audit_log`, endpoints.
4. Run exit gate.

**Acceptance:** CSV/JSON round-trip; integrity proof verifies for authentic exports and fails for tampered ones; tenant-scoped.

---

### Task 5: Compliance evidence pack (M57-04)

**Files:**
- Create: `src/hiveplane/reporting/evidence.py`
- Modify: `src/hiveplane/api/reporting.py`, `src/hiveplane/api/app.py`, `src/hiveplane/reporting/__init__.py`
- Test: `tests/test_reporting_evidence.py`, additions to `tests/test_api_reporting.py`

**Interfaces:**
- `EvidencePackService(reporting_store, approval_service, certification_store, cost_service, *, signing_key: Ed25519PrivateKey|None=None, key_id="reporting", clock=None)`:
  - `generate(ctx, *, period_start, period_end) -> EvidencePack` — files: `approvals.json` (records in period), `attestations.json` (certification records in period), `spend.json` (showback for the period), `index.json` (manifest of file digests + period + counts). Sign the canonical digest with Ed25519 (reuse `hiveplane.artifacts.export`/`hiveplane.certification.signing` primitives; add a reporting-specific domain-separated payload `hiveplane/reporting/evidence/v1`).
  - `verify(pack: EvidencePack, public_key: Ed25519PublicKey) -> bool`.
  - `list(ctx)`, `get(pack_id, *, ctx)`.
- API `POST /compliance/evidence-pack` (REPORTS_MANAGE) body `{period_start, period_end}`; `GET /compliance/evidence-packs`; `GET /compliance/evidence-packs/{pack_id}`.

**Steps:**
1. Failing `tests/test_reporting_evidence.py`: seeded approvals/attestations/spend produce a pack whose files and manifest digests are correct; signature verifies and tampering fails; persisted + audited.
2. Implement `evidence.py`; wire service/endpoints.
3. Run exit gate.

**Acceptance:** Pack contains approvals + attestations + spend for the period; verifies offline against the public key; exists over HTTP.

---

### Task 6: Per-tenant data retention (M57-05)

**Files:**
- Create: `src/hiveplane/reporting/retention.py`
- Create: `src/hiveplane/persistence/migrations/versions/0033_audit_anchor.py` (`down_revision="0032"`)
- Modify: `src/hiveplane/persistence/audit.py`, `src/hiveplane/persistence/postgres_audit.py`, `src/hiveplane/persistence/models.py` (`AuditAnchorRow`), `src/hiveplane/cost/store.py`, `src/hiveplane/auth/store.py`, `src/hiveplane/delivery/store.py`, `src/hiveplane/execution/store.py`, `src/hiveplane/persistence/run_store.py`, `src/hiveplane/api/reporting.py`, `src/hiveplane/api/app.py`, `src/hiveplane/reporting/__init__.py`
- Test: `tests/test_reporting_retention.py`, additions to `tests/test_persistence_audit.py`/`test_persistence_postgres_audit.py`, `tests/test_api_reporting.py`, store tests for the new delete methods, migration 0033 test

**Interfaces (author-specified — use these exact names):**
- `AuditChain.verify(records, *, anchor: str = _NULL_HASH) -> int | None` (default preserves current behavior).
- `AuditLog` protocol gains `prune(*, before: datetime, protect: Callable[[AuditRecord], bool] | None = None, ctx=DEFAULT_CONTEXT) -> int` and `anchor() -> str`. Prune removes the **global leading prefix** of records with `created_at < before`, stopping at the first record where `protect(record)` is True (never a middle record). On prune, set the anchor to the hash of the last deleted record (or keep `_NULL_HASH` if none). `verify()` uses `anchor()` as the chain genesis. Implement in `InMemoryAuditLog` (in-memory anchor attribute) and `PostgresAuditLog` (single-row global `AuditAnchorRow`; migration `0033`).
- New store delete methods (each returns the deleted count; tenant-scoped; both in-memory and Postgres impls):
  - `CostStore.delete_events_before(*, cutoff: datetime, tenant_id: str) -> int` (dropped cost events).
  - `AuthStore.delete_events_before(*, cutoff: datetime, tenant_id: str) -> int` (logins + access events).
  - `DeliveryStore.delete_attempts_before(*, cutoff: datetime, tenant_id: str) -> int`.
  - `RunStore.delete_terminal_before(*, cutoff: datetime, tenant_id: str, ctx=DEFAULT_CONTEXT) -> int` — only runs in a terminal state (`COMPLETED|FAILED|CANCELLED`) with `updated_at < cutoff`; remove the whole run bundle (events/usage/deliveries). Implement for `InMemoryRunStore`, the JSON store, and `PostgresRunStore`.
- `RetentionEnforcer(reporting_store, retention_service, run_store, audit_log, cost_store, auth_store, delivery_store, *, clock=None)`:
  - `set_policy(tenant_id, data_class: RetentionDataClass, retain_days: int, legal_hold=False, *, actor="operator") -> RetentionPolicy` — deterministic `policy_id = f"retention:{tenant_id}:{data_class.value}"` truncated to 64; persisted via the existing artifact `RetentionService.set_policy` (`app.state.retention_service`); `policies(tenant_id)` delegates to its `policies`.
  - `purge_due(*, tenant_id=None, at=None) -> RetentionPurgeResult` — for each configured policy: `RUNS` → `run_store.delete_terminal_before`; `LOGS` → `auth_store.delete_events_before` + `delivery_store.delete_attempts_before`; `ARTIFACTS` → `retention_service.purge_due`; `METERING` → `cost_store.delete_events_before`; `AUDIT` → `audit_log.prune(before=cutoff, protect=<record whose tenant has no expired audit policy or is under legal hold>)`. Never delete any class whose policy has `legal_hold=True`. Audit each class purge as `"retention.purged"` with counts. `RetentionPurgeResult(classes: list[StorePurgeCount], completed_at)` already exists in `models.py` (Task 1).
- API `GET /retention/policies` (FLEET_READ) and `PUT /retention/policies/{data_class}` (upsert; REPORTS_MANAGE); `POST /retention/enforce` (REPORTS_MANAGE) runs `purge_due`.

**Steps:**
1. Failing `tests/test_reporting_retention.py`: set policies; seed expired runs/artifacts/metering/logs; assert `purge_due` deletes exactly the expired, skips legal hold, and audits.
2. Audit prune tests: append records, prune an expired prefix, assert `verify()` still true and `records()` excludes pruned; anchor persists across a Postgres reopen.
3. Implement anchor changes to `audit.py`/`postgres_audit.py` (keep every existing test green — `verify()` with no anchor must behave identically), then `retention.py`.
4. Add endpoints + wiring; run exit gate.

**Acceptance:** Retention policies are per tenant per data class; `purge_due` deletes expired data across runs/logs/artifacts/metering on schedule, honors legal hold, and prunes audit while the chain stays verifiable.

---

### Task 7: PII scrubbing (M57-06)

**Files:**
- Create: `src/hiveplane/reporting/pii.py`
- Modify: `src/hiveplane/persistence/audit.py` (scrub-before-hash hook), `src/hiveplane/persistence/postgres_audit.py`, `src/hiveplane/execution/wiring.py` (`build_audit_log`), `src/hiveplane/artifacts/service.py` (scrub artifact content on capture), `src/hiveplane/api/reporting.py`, `src/hiveplane/api/app.py`, `src/hiveplane/reporting/__init__.py`
- Test: `tests/test_reporting_pii.py`, additions to audit tests

**Interfaces:**
- `PII_PATTERNS`: email, phone, SSN, credit-card (Luhn-ish), IPv4 — compiled regexes with named kinds.
- `PIIScrubber(patterns=None, *, salt="hiveplane-pii", enabled=True)` with `scrub(text: str|None) -> ScrubResult` (redacted text; `redactions` counts by kind; masks with `[REDACTED:<kind>]`), `scrub_hash(value: str) -> str` (salted sha256 of the raw value for audit fields that must keep a verifiable reference).
- `ScrubbingAuditLog(audit_log, scrubber)` implementing `AuditLog`: `append` scrubs `detail` before delegating (scrub-before-hash); `records`/`verify`/`prune`/`anchor` delegate.
- `build_audit_log` wraps the backend in `ScrubbingAuditLog` when `settings.reporting.pii_enabled`.
- Artifact capture scrubs text content when PII enabled.
- API `POST /pii/scrub` (REPORTS_MANAGE) body `{text}` → `ScrubResult`.

**Steps:**
1. Failing `tests/test_reporting_pii.py`: each pattern redacts; non-PII untouched; `scrub(None)`; redaction counts.
2. Failing audit test: with `ScrubbingAuditLog`, appended record's `detail` is redacted and `verify()` is true; raw PII never appears in `records()`.
3. Implement `pii.py`; wire the scrubbing wrapper and artifact hook; add endpoint.
4. Run exit gate.

**Acceptance:** PII is scrubbed from audit detail/artifacts per policy; the audit chain still verifies because scrub-before-hash is enforced; raw PII never persists.

---

### Task 8: Tenant purge & purge certificate (M57-07)

**Files:**
- Create: `src/hiveplane/reporting/purge.py`
- Modify: `src/hiveplane/api/reporting.py`, `src/hiveplane/api/app.py`, `src/hiveplane/reporting/__init__.py`, `src/hiveplane/tenancy/store.py` (optional admin delete helpers)
- Test: `tests/test_reporting_purge.py`, additions to `tests/test_api_reporting.py`

**Interfaces (author-specified; plan amended 2026-09-27):**
- `PurgeTarget` = a frozen record `PurgeTarget(name: str, purge: Callable[[str], int])` (name is the store label recorded in the certificate; callable deletes all of a tenant's data and returns the deleted count). `PurgeTargets` = a sequence of `PurgeTarget`.
- `TenantPurgeService(reporting_store, targets: PurgeTargets, *, legal_hold_check: Callable[[str], bool], signing_key=None, key_id="reporting", clock=None)` with `bind_audit(audit)`:
  - `purge(ctx, tenant_id, *, actor="operator") -> PurgeRecord`:
    - If `legal_hold_check(tenant_id)` is True, delete nothing and raise `PurgeIncompleteError`.
    - Invoke each target's `purge(tenant_id)`, collecting `StorePurgeCount(name, deleted)`; total = sum.
    - Build a signed `PurgeCertificate(scope="tenant", stores=..., total_deleted=..., verifier=actor, signature=PackSignature)` using the same Ed25519 domain-separated scheme as evidence packs (`hiveplane/reporting/evidence/v1` → use a purge-specific prefix `hiveplane/reporting/purge/v1`).
    - Persist a `PurgeRecord` and audit `"tenant.purged"`. Return the record.
  - `verify(record, public_key) -> bool`; `list(ctx)`.
- **Scope decision (deliberate deviation):** tenant purge covers the mutable tenant-scoped stores wired at dispatch: runs, artifacts, metering (cost events/periods/alerts), logs (auth keys/logins/access), delivery (attempts/decisions/preferences), tenancy (tenant/teams/memberships), and reporting reports/exports/evidence (NOT the purge ledger, which is the certificate of record). The **global tamper-evident audit log is not deleted** by tenant purge: deleting one tenant's rows would break the global hash chain for every other tenant. The audit store target is recorded as `retained` (deleted=0) in the certificate and documented. Remaining core stores (certs/triggers/pipelines/secrets) are covered by M58 full isolation.
- New per-store methods required: `RunStore.purge_tenant(tenant_id, *, ctx) -> int` (all runs/bundles for the tenant), `CostStore.purge_tenant(tenant_id) -> int`, `AuthStore.purge_tenant(tenant_id) -> int`, `DeliveryStore.purge_tenant(tenant_id) -> int`, `ArtifactService.purge_tenant(tenant_id) -> int` (deletes blobs + metadata), `TenantStore.purge_tenant(ctx, tenant_id) -> int`; plus reporting store `purge_tenant_reports(tenant_id) -> int` (report runs/schedules/exports/evidence only). Implement in memory + Postgres where both exist. `RunStore.purge_tenant` must delete artifact-bearing run dependents only after artifacts are purged (wire artifacts target BEFORE runs) or skip artifact-bearing runs — keep consistent with Task 6.
- API `POST /tenants/{tenant_id}/purge` (REPORTS_MANAGE; system/admin only) → `PurgeRecord`; `GET /tenants/{tenant_id}/purge-records` (FLEET_READ).

**Steps:**
1. Failing `tests/test_reporting_purge.py`: seed data across stores for two tenants; purge tenant A; assert only A's data is gone, certificate counts match, signature verifies, `PurgeRecord` persisted, audit emitted, tenant B untouched; legal hold blocks with no certificate.
2. Implement `purge.py`; wire service + endpoints.
3. Run exit gate.

**Acceptance:** Purge removes all of a tenant's data across stores, leaves a signed verifiable purge certificate, is audited, and is blocked by legal hold.

---

### Task 9: M57 test/acceptance pass, docs & changelog (M57-08, #448)

**Files:**
- Create: `tests/test_reporting_acceptance.py`
- Modify: `docs/design/reporting-tenancy-distribution-design.md` (status → implemented + decisions), `docs/USER_GUIDE.md`, `CHANGELOG.md`, `docs/wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md` (check M57 boxes)

**Steps:**
1. Write `tests/test_reporting_acceptance.py` covering the five M57 acceptance criteria end-to-end through `create_app()`: digest content, audit export proof, evidence pack, retention schedule purge + certificate, PII scrub order; plus cross-tenant denial on every new endpoint (`X-Hiveplane-Tenant`).
2. Update design doc: record decisions (global chain + pruning anchor; scrub-before-hash; deterministic retention policy ids; purge certificate signing) and set status.
3. Add a USER_GUIDE section (digest, audit export, evidence pack, retention/PII, purge) and a CHANGELOG `### Added (M57 — ...)` entry ending with the exact test/coverage/lint line.
4. Run the full gate: `python -m pytest`, `python -m pytest --cov=src/hiveplane --cov-report=term-missing`, `python -m ruff check`, `python -m mypy src/ tests/`; confirm coverage >95%.

**Acceptance:** All M57 acceptance criteria are proven by tests; docs and changelog updated; exit gate green.

---

## See Also

- [D38 design](../design/reporting-tenancy-distribution-design.md)
- [WBS v0.2.0 Part 17](../wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md)
- [M25-01 plan](m25-01-tenancy-scoping.md) — scoping conventions this plan follows
