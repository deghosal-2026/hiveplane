# WBS v0.2.0 — Part 1: Foundation & Reconciliation

**Milestones:** M25–M26 · **Part:** 1 of 19

## Goal

Extend the v0.1.0 data model to represent the fleet primitives every later part depends on — teams/tenants, triggers, pipelines, policy packs, secrets, workers, artifacts, cost periods, and reconciliation state — then build the desired-state reconciliation loop that makes the fleet declarative (GitOps).

## M25 — Fleet Control Data Models & Migrations

**Objective:** Add the persistent schema and typed core models for all v0.2.0 primitives, with forward-only Alembic migrations that keep an existing v0.1.0 database bootable.

**Work items:**

- [x] [#149](https://github.com/deghosal-2026/hiveplane/issues/149) — M25-01 — Team/tenant model (org, team, membership, attribution keys) + tenant scoping on existing tables
- [x] [#150](https://github.com/deghosal-2026/hiveplane/issues/150) — M25-02 — Trigger model (source, event filter, dedup key, cooldown, rate limit, task template, admission rule, history)
- [x] [#151](https://github.com/deghosal-2026/hiveplane/issues/151) — M25-03 — Pipeline model (DAG nodes, edges, handoff mappings, per-step gates, pipeline budget)
- [x] [#152](https://github.com/deghosal-2026/hiveplane/issues/152) — M25-04 — Policy pack model (versioned, inheritable, lint status) + policy decision record (reason + originating rule id)
- [x] [#153](https://github.com/deghosal-2026/hiveplane/issues/153) — M25-05 — Secret model (encrypted-at-rest, scope, rotation metadata) and secret-reference resolution types
- [x] [#154](https://github.com/deghosal-2026/hiveplane/issues/154) — M25-06 — Worker model (identity, capabilities, lease, heartbeat, last-seen, status)
- [x] [#155](https://github.com/deghosal-2026/hiveplane/issues/155) — M25-07 — Artifact model (run linkage, location, size, retention policy, hash)
- [x] [#156](https://github.com/deghosal-2026/hiveplane/issues/156) — M25-08 — Cost period model (day/week/month buckets) and metering event schema
- [x] [#157](https://github.com/deghosal-2026/hiveplane/issues/157) — M25-09 — Reconciliation/desired-state model (declared spec, observed state, drift record, last reconcile)
- [x] [#158](https://github.com/deghosal-2026/hiveplane/issues/158) — M25-10 — Forward-only Alembic migration with backfill for existing v0.1.0 data; auto-migration on startup preserved
- [x] [#159](https://github.com/deghosal-2026/hiveplane/issues/159) — M25-11 — Model unit tests + migration up/down test against a seeded v0.1.0 database

**Test ticket:** [x] [#160](https://github.com/deghosal-2026/hiveplane/issues/160) — Test cases for Fleet Control Data Models & Migrations

**Deliverables:**
- Typed Pydantic/SQLAlchemy models for all of the above under `src/hiveplane/`
- One coherent migration series; a v0.1.0 database upgrades cleanly with no data loss
- `docs/design/fleet-control-data-model-design.md` documenting the schema and scoping rules

**Acceptance criteria:**
- [x] A v0.1.0 database migrates to the v0.2.0 schema on startup with existing runs/certs intact
- [x] Every new table is tenant-scoped where tenant isolation applies (verified by test)
- [x] Model validation rejects malformed trigger/pipeline/policy definitions with actionable errors
- [x] Coverage ≥ 95% for the new model and migration modules (95% with a database, as CI runs)

**Done when:** the new schema is live, migrates automatically, and every later part can persist its state without further foundational migrations.

> **Status:** M25 complete. All 11 work items and the test ticket landed; migrations `0003`
> (tenancy) and `0004` (fleet-control primitives) verified up/down against PostgreSQL 16;
> 1019 tests pass with a database (990 without), mypy strict and ruff clean.

**Dependencies:** v0.1.0 state store (Part 9 of v0.1.0).

**Notes / risks:** schema churn is the main risk — freeze the model surface at M25 exit and require a follow-on migration for changes. Tenant scoping is security-critical; review the scoping matrix before M25 closes.

## M26 — Desired-State Reconciliation (GitOps) Core

**Objective:** Implement the controller that continuously reconciles declared desired fleet state (from git or the API) against observed state — registering missing agents, re-certifying drifted configs, quarantining unmanaged workloads, and enforcing policy-pack versions.

**Work items:**

- [ ] [#161](https://github.com/deghosal-2026/hiveplane/issues/161) — M26-01 — Desired-state spec format (fleet manifest set: workloads, policies, triggers, budgets) + loader from git repo or directory
- [ ] [#162](https://github.com/deghosal-2026/hiveplane/issues/162) — M26-02 — Reconciliation controller loop (observe → diff → plan → act → record) with dry-run mode
- [ ] [#163](https://github.com/deghosal-2026/hiveplane/issues/163) — M26-03 — Reconcile actions: register missing workload, deregister removed workload, update manifest, trigger re-cert on config change, quarantine unmanaged workload
- [ ] [#164](https://github.com/deghosal-2026/hiveplane/issues/164) — M26-04 — Drift record + reconcile history persisted and surfaced via API/CLI (`hiveplane reconcile status`)
- [ ] [#165](https://github.com/deghosal-2026/hiveplane/issues/165) — M26-05 — Conflict policy (declared-wins vs. observed-wins per field) and guardrails against destructive reconcile
- [ ] [#166](https://github.com/deghosal-2026/hiveplane/issues/166) — M26-06 — Git source integration (repo URL, path, ref, auth) with poll/webhook trigger; no writes back to git
- [ ] [#167](https://github.com/deghosal-2026/hiveplane/issues/167) — M26-07 — Controller concurrency safety (advisory lock / leader-aware) so a second replica cannot double-act
- [ ] [#168](https://github.com/deghosal-2026/hiveplane/issues/168) — M26-08 — Tests: reconcile idempotency, add/remove/update paths, dry-run safety, concurrent-controller safety

**Test ticket:** [#169](https://github.com/deghosal-2026/hiveplane/issues/169) — Test cases for Desired-State Reconciliation (GitOps) Core

**Deliverables:**
- `hiveplane.reconcile` package with a controller, planner, and action executor
- `hiveplane reconcile status|plan|apply` CLI
- `docs/design/reconciliation-design.md`

**Acceptance criteria:**
- [ ] Deleting a workload from desired state deregisters it (never deletes its history)
- [ ] Editing a cert threshold in desired state triggers re-certification on the next reconcile
- [ ] Running reconcile twice produces no second action set (idempotent)
- [ ] Dry-run reports the plan without mutating state
- [ ] A second controller replica does not execute the same actions (leader/advisory lock verified)

**Done when:** the fleet can be declared in git and the controller converges actual state to it, safely and idempotently.

**Dependencies:** M25; registry/certification services from v0.1.0.

**Notes / risks:** this is the Kubernetes-defining pattern — do not let it become a destructive "apply" tool. Default to additive/soft actions; gate destructive actions behind explicit policy.

## Exit Gate (M25, M26)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (data model, reconciliation design, PRD/WBS references)
- [ ] All M25–M26 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Fleet Control & Scheduling theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars B, K
- [v0.2.0 index](wbs-v0.2.0-index.md)
