# WBS v0.2.0 — Part 11: Secrets, Identity & Distributed Workers

**Milestones:** M45–M46 · **Part:** 11 of 19

## Goal

Give the plane a secrets store and an identity model, then make execution distributed: remote workers that register, heartbeat, and execute runs under leases — with identity so a rogue host cannot run fleet work.

## M45 — Secrets Store, Rotation, RBAC-lite & Access Audit

**Objective:** Provide a per-tenant encrypted secret store injected at runtime (never entering agent context), key rotation, operator roles with scoped API keys, and an access audit.

**Work items:**

- [x] [#333](https://github.com/deghosal-2026/hiveplane/issues/333) — M45-01 — Encrypted secret store (per-tenant scope), encrypted at rest with a managed key
- [x] [#334](https://github.com/deghosal-2026/hiveplane/issues/334) — M45-02 — Runtime injection: env-var and file mounts; secrets resolved at execution boundary
- [x] [#335](https://github.com/deghosal-2026/hiveplane/issues/335) — M45-03 — Context protection: secrets never enter agent context, logs, traces, or audit events (verified by test)
- [x] [#336](https://github.com/deghosal-2026/hiveplane/issues/336) — M45-04 — Rotation: rotate secrets without redeploying workloads; versioned secret refs
- [x] [#337](https://github.com/deghosal-2026/hiveplane/issues/337) — M45-05 — RBAC-lite: roles (admin/approver/viewer), per-tenant membership, scoped API keys
- [x] [#338](https://github.com/deghosal-2026/hiveplane/issues/338) — M45-06 — UI login + API-key authn; role checks on approve/promote/kill-switch actions
- [x] [#339](https://github.com/deghosal-2026/hiveplane/issues/339) — M45-07 — Access audit: operator login history + API-key usage analytics
- [x] [#340](https://github.com/deghosal-2026/hiveplane/issues/340) — M45-08 — CLI/API for secret and key management
- [x] [#341](https://github.com/deghosal-2026/hiveplane/issues/341) — M45-09 — Tests: secret never appears in logs/traces/context; viewer cannot approve; rotation works; scoped key is limited

**Test ticket:** [#342](https://github.com/deghosal-2026/hiveplane/issues/342) — Test cases for Secrets Store, Rotation, RBAC-lite & Access Audit

**Deliverables:**
- `hiveplane.secrets` package + RBAC/authn layer
- `docs/design/secrets-rbac-design.md` and `docs/design/secrets-rbac-design.md`

**Acceptance criteria:**
- [x] A secret never appears in logs, traces, or agent context (verified by automated test)
- [x] Rotating a secret takes effect for the next run without redeploy
- [x] A viewer-role operator cannot approve, promote, or trigger a kill switch (403)
- [x] A scoped API key cannot exceed its scope
- [x] Login history and API-key usage are recorded and queryable

**Done when:** secrets are safe, rotation is operational, and operator actions are role-scoped and audited.

> **Status:** M45 complete. `hiveplane.secrets` provides a per-tenant envelope-encrypted secret vault (AES-256-GCM data keys wrapped by a local key-provider key; ciphertext only at rest), versioned refs `secret://<tenant>/<name>@<version>`, rotation that takes effect next run, and boundary injection as env vars or tmpfs files. A per-run `Redactor` plus `RedactionLogFilter` guarantee secrets never reach context, logs, traces, audit, fan-out, or artifacts (asserted by absence tests, fail-closed). `hiveplane.auth` adds RBAC-lite (admin/approver/viewer), scoped API keys that narrow but never widen a role, and server-side authorization on approve, promote, and kill-switch actions (401/403 from direct API calls). Access audit records logins and privileged actions. Ships API (`/secrets`, `/keys`, `/auth/*`, `/audit/access`), CLI (`secrets`, `keys`, `auth whoami`), tables `secret_vault`/`secret_vault_versions`/`api_keys`/`access_audit` (migration `0023`; new tables avoid the M25 `secrets` table). Issues #333–#342 closed; 2025 tests pass with a database, coverage 95.08%, ruff and mypy strict clean.

**Dependencies:** M25 (secret/identity models); v0.1.0 redaction.

**Notes / risks:** secret leakage is the highest-severity risk in this part — write adversarial tests that assert absence in every sink. RBAC must be enforced server-side, never only in the UI.

## M46 — Worker Daemon & Worker Identity

**Objective:** Add `hiveplane worker` — remote workers that register, heartbeat, and execute runs under leases, with lease expiry reassigning work, and worker identity (signed tokens/mTLS) so unauthenticated hosts are refused.

**Work items:**

- [x] [#343](https://github.com/deghosal-2026/hiveplane/issues/343) — M46-01 — Worker daemon: `hiveplane worker` connects to the plane, advertises capabilities, pulls assigned runs
- [x] [#344](https://github.com/deghosal-2026/hiveplane/issues/344) — M46-02 — Registration + heartbeat: worker liveness, capabilities, load; stale workers are marked unhealthy
- [x] [#345](https://github.com/deghosal-2026/hiveplane/issues/345) — M46-03 — Lease-based execution: runs are leased; lease expiry → automatic reassignment
- [x] [#346](https://github.com/deghosal-2026/hiveplane/issues/346) — M46-04 — Crash detection: killed/unresponsive worker's leased runs are reclaimed and rescheduled
- [x] [#347](https://github.com/deghosal-2026/hiveplane/issues/347) — M46-05 — Worker identity: signed worker tokens and/or mTLS; unauthenticated/rogue hosts are refused
- [x] [#348](https://github.com/deghosal-2026/hiveplane/issues/348) — M46-06 — Worker lifecycle: drain, maintenance, deregistration
- [x] [#349](https://github.com/deghosal-2026/hiveplane/issues/349) — M46-07 — Worker fleet view + CLI (`hiveplane workers list`)
- [x] [#350](https://github.com/deghosal-2026/hiveplane/issues/350) — M46-08 — Tests: run executes on a second host; kill worker → lease expiry reassigns; rogue worker without token refused; drain works

**Test ticket:** [#351](https://github.com/deghosal-2026/hiveplane/issues/351) — Test cases for Worker Daemon & Worker Identity

**Deliverables:**
- `hiveplane.worker` daemon + worker registry
- Worker identity/token issuance
- `docs/design/fleet-execution-design.md`

**Acceptance criteria:**
- [x] A run executes on a remote worker daemon on a second host
- [x] Killing the worker mid-run causes lease expiry and reassignment to a healthy worker
- [x] A worker without a valid identity is refused registration and execution
- [x] Draining a worker completes in-flight work and stops new assignments
- [x] Worker liveness and leases are visible in the fleet view

**Done when:** the plane executes runs across multiple authenticated workers with crash-safe lease reassignment.

> **Status:** M46 complete. `hiveplane.worker` provides a stateless worker daemon that enrolls, registers with a signed HMAC-SHA256 identity token (bound to worker id + tenant, with expiry/revocation and rogue-host refusal), advertises capabilities, heartbeats load, and executes leased runs through the adapter path. The `WorkerRegistry` grants time-boxed leases with a **fencing token**; stale-fence reports are rejected (zombie defense), lease expiry and missed-heartbeat unhealthiness reassign work with `attempt += 1` and `reassigned_from` attribution, and drain/maintenance/deregister lifecycle transitions are supported. Fleet view (`GET /workers`, `hiveplane workers list`) shows state, load, and active leases. Ships API `/workers/enroll|register|{id}/heartbeat|{id}/leases|{id}/drain` and `DELETE /workers/{id}`, CLI `workers list|enroll` and `worker`, table `worker_tokens` (migration `0024`). Issues #343–#351 closed; 2048 tests pass with a database, coverage 95.06%, ruff and mypy strict clean.

**Dependencies:** M25 (worker model), M45 (identity); v0.1.0 run lifecycle.

**Notes / risks:** lease semantics are the hard part — choose idempotent run steps so reassignment cannot double-apply side effects. Worker identity is mandatory before this ships; an unauthenticated worker is a security hole.

## Exit Gate (M45, M46)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (secrets, RBAC, workers, deployment guide)
- [x] All M45–M46 issues done and closed
- [x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Secrets & Identity, Fleet Control themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars H, K
- [v0.2.0 index](wbs-v0.2.0-index.md)
