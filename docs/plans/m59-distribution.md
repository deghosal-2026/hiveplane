# M59 — Distribution & Supply Chain Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship M59 of v0.2.0 — install the plane on a real cluster with a verifiable supply chain, recover control-plane state from backup, install offline, and offer a demo profile and (stretch) federation.

**Source of truth:** WBS `docs/wbs/v0.2.0/wbs-v0.2.0-part18-distribution-replay.md` (M59 items #458–#467), PRD 05 "Distribution & First Run", PRD 09 roadmap pillars L/Q, design doc `docs/design/reporting-tenancy-distribution-design.md` (D38 distribution section).

**Decisions:**
- Python-testable work (backup/restore, demo profile, federation) gets full unit/integration coverage and CLI wiring.
- Chart/CI/scripts are verified by `helm lint`/`helm template` render tests (skipped when `helm` is absent) and shell syntax checks; real k3d/cosign/SBOM runs are a documented runbook + CI workflow.
- Federation is behind a feature flag (`HIVEPLANE_FEDERATION__ENABLED=false`) and never blocks the release.

## Global Constraints
- Forward-only; no comments unless the surrounding file already uses them. Ruff/mypy strict at the end.
- `ctx.scopes`/`ctx.require` for any tenant-scoped store (federation status is control-plane-global).
- Tests carry the work-item id in the module docstring (`(M59-0x)`).
- Full `pytest` + coverage + ruff + mypy run once, at the end.

---

## Tasks

### Task 1 — Backup/restore (M59-03)
- `src/hiveplane/backup/` package: `models.py` (`BackupManifest`, `BackupFile`, integrity digest), `service.py` (`BackupService.create/restore/verify` over a pluggable set of `BackupTarget`s), `factory.py`, `errors.py`.
- Integrity: canonical manifest (algorithm sha256, per-file digests, logical clock, control-plane version, alembic head) signed Ed25519 (reuse `artifacts.export`/`certification.signing` primitives); `verify` recomputes and checks the signature.
- Targets: run store, registry store, cost/audit as JSON snapshots (in-memory + Postgres via the persistence session), plus a migration-head record so restore checks schema compatibility.
- CLI: `hiveplane backup create|verify|restore`.
- Tests: create→verify→wipe→restore round-trip (in-memory and pg_engine); tampered manifest fails; restore refuses a schema mismatch; determinism.

### Task 2 — Demo profile (M59-07)
- `src/hiveplane/demo/` package: `seed.py` (`DemoProfile` seeding a screenshot-ready fleet: tenants, workloads, certifications, runs with varied outcomes, drift/quarantine, spend, approvals) + seeded failure scenarios.
- CLI: `hiveplane demo seed [--profile default]`, `hiveplane demo reset`.
- Tests: seed is idempotent/deterministic; a seeded fleet returns non-empty catalog/runs/cost/drift through the API.

### Task 3 — Federation (stretch, M59-08)
- `src/hiveplane/federation/` package: `models.py` (`RemotePlane`, `AggregateEntry`), `store.py`, `service.py` (`register`, `list`, `aggregate`), behind `FederationSettings.enabled`.
- API: `POST /federation/planes`, `GET /federation/planes`, `GET /federation/aggregate` (503/disabled when off; ADMIN gates).
- Tests: flag-off returns 503; register + aggregate; duplicate registration is idempotent; secrets are not leaked in the aggregate.

### Task 4 — Helm chart + k3d (M59-01, M59-02)
- `deploy/helm/hiveplane/`: `Chart.yaml`, `values.yaml`, `values.schema.json`, templates (api, ui, postgres, redis, otel/tempo/prometheus/grafana, service, ingress, secret, serviceaccount), `README.md`.
- `deploy/k3d/` scripts + `docs/runbooks/k3d-reference-deploy.md`.
- Test: `tests/test_helm_chart.py` — `helm lint` + `helm template` render and assert the full stack objects exist (skip if `helm` missing); values.schema.json is valid JSON.

### Task 5 — Air-gap, Homebrew/PyPI, release supply chain (M59-04, M59-05, M59-06)
- `scripts/airgap-bundle.sh` + `deploy/release/` docs.
- `deploy/homebrew/hiveplane.rb` formula template; PyPI metadata hardening in `pyproject.toml`; `docs/distribution.md`.
- `.github/workflows/release.yml`: build, `syft` SBOM, `cosign` sign images, SLSA-style provenance, attach artifacts; chart package/publish.
- Tests: shell `sh -n` syntax checks; formula/render smoke assertions.

### Task 6 — M59 tests/docs/exit gate (M59-09, #467)
- `tests/test_m59_*.py` consolidation; design doc status → implemented; WBS checkboxes; `docs/USER_GUIDE.md`; `CHANGELOG.md`.
- Run the full gate once; close #458–#467.

## See Also
- [WBS Part 18](../wbs/v0.2.0/wbs-v0.2.0-part18-distribution-replay.md)
- [D38 design](../design/reporting-tenancy-distribution-design.md)
