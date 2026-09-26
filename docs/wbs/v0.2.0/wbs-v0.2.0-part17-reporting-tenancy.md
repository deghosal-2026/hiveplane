# WBS v0.2.0 — Part 17: Reporting & Multi-Tenancy

**Milestones:** M57–M58 · **Part:** 17 of 19

## Goal

Make the fleet accountable (weekly digest, audit export, compliance evidence, retention/PII purge) and genuinely multi-tenant (isolated budgets, policies, and keys per tenant).

## M57 — Weekly Digest, Audit Export, Compliance Evidence & Retention/PII Purge

**Objective:** Auto-generate a weekly fleet digest, export the audit trail, assemble a compliance evidence pack, and enforce per-tenant data retention with PII scrubbing and tenant purge.

**Work items:**

- [ ] [#440](https://github.com/deghosal-2026/hiveplane/issues/440) — M57-01 — Weekly fleet digest: auto markdown/Slack/email with spend, drift, approvals, top/bottom ROI agents
- [ ] [#441](https://github.com/deghosal-2026/hiveplane/issues/441) — M57-02 — Digest scheduling + per-team routing (integrate M51 notification prefs)
- [ ] [#442](https://github.com/deghosal-2026/hiveplane/issues/442) — M57-03 — Audit export: CSV/JSON export of the tamper-evident audit log with integrity proof
- [ ] [#443](https://github.com/deghosal-2026/hiveplane/issues/443) — M57-04 — Compliance evidence pack: approvals + attestation history + spend for a period, exportable
- [ ] [#444](https://github.com/deghosal-2026/hiveplane/issues/444) — M57-05 — Data retention: per-tenant retention policies across runs, logs, artifacts, audit
- [ ] [#445](https://github.com/deghosal-2026/hiveplane/issues/445) — M57-06 — PII scrubbing: configurable PII detection/redaction in logs and stored artifacts
- [ ] [#446](https://github.com/deghosal-2026/hiveplane/issues/446) — M57-07 — Tenant purge (right-to-delete): scheduled/on-demand purge with a purge certificate
- [ ] [#447](https://github.com/deghosal-2026/hiveplane/issues/447) — M57-08 — Tests: digest content correct; audit export verifies; evidence pack complete; retention purge deletes on schedule; PII scrubbed; purge is auditable

**Test ticket:** [#448](https://github.com/deghosal-2026/hiveplane/issues/448) — Test cases for Weekly Digest, Audit Export, Compliance Evidence & Retention/PII Purge

**Deliverables:**
- `hiveplane.reporting` package (digest, evidence, retention, PII, purge)
- `docs/design/reporting-tenancy-distribution-design.md`, `docs/design/reporting-tenancy-distribution-design.md`

**Acceptance criteria:**
- [ ] The weekly digest generates automatically with accurate spend/drift/approval/ROI content
- [ ] Audit export round-trips and its integrity proof verifies
- [ ] The compliance evidence pack contains approvals + attestations + spend for the period
- [ ] Retention purge deletes tenant data on schedule and leaves a purge certificate
- [ ] PII is scrubbed from logs/artifacts per policy

**Done when:** the fleet reports on itself and can meet retention/compliance obligations, including tenant deletion.

**Dependencies:** M35 (audit/attestation), M49 (spend), M34 (drift), M45 (access audit).

**Notes / risks:** purge must be complete and verifiable — a partial purge is a compliance failure. PII scrubbing must not corrupt the tamper-evident audit chain; scrub before hashing where required and document the ordering.

## M58 — Multi-Tenant Isolation

**Objective:** Make tenant isolation real: per-tenant budgets, policies, keys, and data scoping, with cross-tenant access impossible and verified.

**Work items:**

- [ ] [#449](https://github.com/deghosal-2026/hiveplane/issues/449) — M58-01 — Tenant isolation across all stores (runs, certs, triggers, pipelines, secrets, artifacts, cost)
- [ ] [#450](https://github.com/deghosal-2026/hiveplane/issues/450) — M58-02 — Per-tenant budgets + spend caps (integrate M49/M50)
- [ ] [#451](https://github.com/deghosal-2026/hiveplane/issues/451) — M58-03 — Per-tenant policy packs + defaults (integrate M40)
- [ ] [#452](https://github.com/deghosal-2026/hiveplane/issues/452) — M58-04 — Per-tenant API keys, roles, and membership (integrate M45)
- [ ] [#453](https://github.com/deghosal-2026/hiveplane/issues/453) — M58-05 — Cross-tenant denial: every API/CLI/UI path is tenant-scoped and denies cross-tenant access
- [ ] [#454](https://github.com/deghosal-2026/hiveplane/issues/454) — M58-06 — Tenant admin operations: create/suspend tenant, quota assignment
- [ ] [#455](https://github.com/deghosal-2026/hiveplane/issues/455) — M58-07 — Isolation test suite: adversarial attempts to read/write another tenant's data all fail
- [ ] [#456](https://github.com/deghosal-2026/hiveplane/issues/456) — M58-08 — Tests: budget/policy/key isolation verified; suspended tenant cannot submit; cross-tenant read/write denied

**Test ticket:** [#457](https://github.com/deghosal-2026/hiveplane/issues/457) — Test cases for Multi-Tenant Isolation

**Deliverables:**
- Tenant scoping across the plane + tenant admin
- `docs/design/reporting-tenancy-distribution-design.md`

**Acceptance criteria:**
- [ ] A tenant can only see and act on its own resources (verified by an adversarial suite)
- [ ] Per-tenant budgets/policies/keys are isolated and enforced
- [ ] A suspended tenant cannot submit runs or receive deliveries
- [ ] Cross-tenant read/write attempts return 403/404 consistently
- [ ] Tenant admin can create/suspend tenants and assign quotas

**Done when:** multiple tenants can share one plane with verified isolation of data, budget, policy, and identity.

**Dependencies:** M25 (tenant scoping), M45 (identity), M49/M50 (budget), M40 (policy).

**Notes / risks:** isolation is security-critical — build the adversarial suite first and treat any leak as a release blocker. Audit every query for tenant scoping.

## Exit Gate (M57, M58)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (reporting, retention/PII, multi-tenancy, admin guide)
- [ ] All M57–M58 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Reporting & Compliance, Secrets & Identity themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars L, P
- [v0.2.0 index](wbs-v0.2.0-index.md)
