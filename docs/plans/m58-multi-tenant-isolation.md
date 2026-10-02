# M58 — Multi-Tenant Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship M58 of v0.2.0 — make tenant isolation real: per-tenant budgets, policies, keys, and data scoping across every store; a suspended tenant cannot act; cross-tenant read/write is denied with consistent 403/404 on every API/CLI/UI path; proven by an adversarial isolation suite.

**Source of truth:** [`docs/design/reporting-tenancy-distribution-design.md`](../design/reporting-tenancy-distribution-design.md) (D38, "Multi-tenant isolation"), [`docs/wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md`](../wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md) (M58 items #449–#457), PRD 05 "Secrets & Identity", PRD 09 roadmap pillars L/P.

**Architecture:** Three seams close isolation. (1) **One boundary resolver**: `get_tenant_context` becomes the single place that turns a request into a `TenantContext`; when `auth.enabled` it reconciles the authenticated principal's tenant with the `X-Hiveplane-Tenant` header (principal must match, `system` may override) and derives role from the principal; when auth is disabled it keeps today's permissive header behavior so field/demo flows and the existing test suite keep working. Tenancy errors map to HTTP 403/404. (2) **Stores own the boundary**: every store that today takes a raw `tenant_id` (secrets, artifacts, cost, delivery, events, corpus, worker, auth) gains a `ctx: TenantContext` and calls `ctx.require(...)` on writes / `ctx.scopes(...)` on reads — the same contract already used by runs/registry/triggers/pipelines. (3) **Enforcement upstream**: tenant lifecycle (`status`, quota) lives on `Tenant`; budget/policy/key isolation threads the run's tenant through `BudgetService`, `PolicyEngine`, and `AuthService`. A new adversarial suite (Task 8) is the release gate.

**Decisions locked with the human partner (2026-09-29):**
- Auth binding is enforced **only when `settings.auth.enabled`**; disabled keeps the permissive header (existing tests depend on it).
- Policy packs stay **in-memory but tenant-keyed** `(tenant, name)`; Postgres persistence is deferred (documented).
- Isolation covers **API + CLI**; the UI already forwards `X-Hiveplane-Tenant` and inherits API isolation.
- The global audit chain, transparency log, kill switch, incident mode, and HA leader lease remain deliberately **global** (documented) — they are not tenant-partitioned.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0, Alembic, FastAPI, pytest, ruff, mypy strict.

## Global Constraints

- **Tenant scoping:** every store/service read filters via `ctx.scopes(tenant_id)`; every write calls `ctx.require(...)`. Store methods take `ctx: TenantContext = DEFAULT_CONTEXT` keyword-only (the M25-01 documented deviation). `SYSTEM_CONTEXT` bypasses.
- **No existence leak:** a cross-tenant read returns `None`/`[]` (renders 404); a cross-tenant write raises `TenantScopeError` (renders 403). Never reveal that another tenant's record exists.
- **Header discipline:** `X-Hiveplane-Tenant` selects the tenant; `X-Hiveplane-Team` the team. The header is **never** trusted as an identity. When `auth.enabled`, `principal.tenant_id` must equal the header tenant (or principal is a system/admin key) else 403. When auth is disabled, behavior is unchanged from today.
- **Direction:** forward-only. No backward-compatibility shims. No comments unless the surrounding file already uses them.
- **Pydantic:** `model_config = ConfigDict(extra="forbid")`; ids `Field(min_length=1, max_length=64)`; operator/name/detail strings `max_length=253`; artifact-like refs `max_length=512`.
- **Persistence:** new ORM columns are added to the model (so migration `0001`'s `create_all` builds fresh DBs) **and** to a new idempotent revision `0034` that adds them to upgraded DBs via inspector checks. New tables are added to the expected set in `tests/test_persistence_schema.py` and (unless global) to the tenant-scoped list.
- **API:** routers use `Annotated` deps from `api/deps.py`; reads gate on `Permission.FLEET_READ`; writes gate on the domain permission. Domain errors map to HTTP statuses via `create_app`'s handler tables.
- **Reuse:** reuse `hiveplane.tenancy.context` helpers (`TenantContext`, `SYSTEM_CONTEXT`, `DEFAULT_CONTEXT`, `context_for_run`), `hiveplane.tenancy.errors`, and the existing `ctx.require`/`ctx.scopes` contract. Do not invent a second scoping mechanism.
- **Tests:** module docstrings carry the work-item id (`(M58-0x)`). In-memory fakes by default; `pg_engine` + `tests/postgres.py` helpers for DB paths. No new pytest markers.
- **Exit gate (every task boundary):** `python -m pytest` green; `python -m ruff check` clean; `python -m mypy src/ tests/` clean; new-module coverage >95%.

## File Structure

**Create**
- `src/hiveplane/tenancy/admin.py` — `TenantAdminService` (+ `TenantQuota`, `TenantStatus` in `models.py`).
- `src/hiveplane/api/tenants.py` — tenant lifecycle router.
- `src/hiveplane/persistence/migrations/versions/0034_tenant_lifecycle.py`
- `tests/test_tenancy_admin.py`, `tests/test_api_tenants.py`, `tests/test_persistence_migration_0034.py`
- `tests/test_m58_isolation.py` — the adversarial suite (Task 8).
- `tests/test_m58_acceptance.py` — end-to-end acceptance (Task 9).

**Modify (by area)**
- Boundary: `src/hiveplane/api/deps.py`, `src/hiveplane/api/app.py` (error tables + router include), `src/hiveplane/config.py` (if a tenant setting is needed).
- Lifecycle: `src/hiveplane/tenancy/models.py`, `src/hiveplane/tenancy/store.py`, `src/hiveplane/persistence/models.py` (`TenantRow.status`).
- Stores: `secrets/store.py`, `artifacts/store.py`, `cost/store.py`, `delivery/store.py`, `events/store.py`, `corpus/store.py`, `worker/store.py`, `auth/store.py`, `certification/store.py`, `mcp/store.py`.
- Services: `secrets/service.py`, `artifacts/service.py`, `cost/service.py` + `cost/depth.py`, `delivery/service.py`, `events/service.py`, `corpus/service.py`, `worker/registry.py`, `auth/service.py` + `auth/keys.py`, `certification/workflow.py`, `execution/service.py`, `execution/admission.py`, `execution/gates.py`, `budget/service.py`, `policy/engine.py`, `policy/packs.py`, `policy/pack_registry.py`.
- Routers: `api/registry.py`, `api/certifications.py`, `api/learning.py`, `api/mcp.py`, `api/progressive.py`, `api/health.py`, `api/cost.py`, `api/workers.py`, `api/delivery.py`, `api/scheduler.py`, `api/identity.py`, `api/artifacts.py`, `api/corpora.py`, `api/events.py`, `api/approvals.py`, `api/policy.py`.
- CLI: `src/hiveplane/cli.py` (global `--tenant`/`--team`, header injection, error mapping).
- Tests: `tests/test_tenant_scoping_isolation.py` (extend matrix), plus the existing store/service/API tests touched by each task.
- Docs: `docs/design/reporting-tenancy-distribution-design.md`, `docs/wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md`, `docs/USER_GUIDE.md`, `CHANGELOG.md`.

---

## Phase 1 — Boundary & lifecycle

### Task 1: Tenancy boundary — error mapping, principal/header reconciliation, tenant status

**Work items:** M58-05 (foundation), M58-06 (status substrate).

**Files:**
- Modify: `src/hiveplane/tenancy/models.py` (add `TenantStatus`, `Tenant.status`), `src/hiveplane/tenancy/store.py` (persist status), `src/hiveplane/persistence/models.py` (`TenantRow.status`), `src/hiveplane/api/deps.py`, `src/hiveplane/api/app.py`
- Create: `src/hiveplane/persistence/migrations/versions/0034_tenant_lifecycle.py`
- Test: `tests/test_persistence_migration_0034.py`, additions to `tests/test_tenant_scoping_isolation.py`

**Interfaces (exact — later tasks depend on these):**
- `TenantStatus(StrEnum)`: `ACTIVE`, `SUSPENDED`.
- `Tenant` gains `status: TenantStatus = TenantStatus.ACTIVE` (frozen model unchanged otherwise).
- `TenantStore` gains `set_status(ctx, tenant_id, status) -> Tenant` (raises `TenantNotFoundError`). `InMemoryTenantStore` + `PostgresTenantStore` implement it; `save_tenant` writes `status` to `TenantRow.status`.
- `TenantRow` gains `status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active", index=True)`.
- `api/deps.py`:
  - New `resolve_tenant(request, principal, settings) -> TenantContext` — the single boundary:
    - `settings.auth.enabled` and principal tenant is not `SYSTEM_TENANT_ID`: if header present and `header != principal.tenant_id` → `HTTPException(403)`; context tenant = `principal.tenant_id`, role = `principal.role`, operator_id = principal.operator_id, team from header.
    - `settings.auth.enabled` and principal is system: header selects tenant; role ADMIN.
    - auth disabled: exactly today's behavior (header tenant, role ADMIN, team from header; absent header → `DEFAULT_CONTEXT`).
  - `get_tenant_context(request)` keeps its signature (used by all existing routers) but delegates to `resolve_tenant` and therefore reconciles when auth is enabled. It must not require a principal when auth is disabled (no 401 for existing tests).
  - `require_permission` unchanged (still permission-only); the tenant cross-check lives in `resolve_tenant`.
- `create_app` error handlers: add `(TenantScopeError, 403)`, `(TenantNotFoundError, 404)`, `(TeamNotFoundError, 404)` to the handler loop.
- `0034_tenant_lifecycle.py`: `down_revision="0033"`; idempotently add `tenants.status` (inspector check) with server_default `active`; `downgrade` drops it if present.

**Steps:**
1. Failing tests: `Tenant` defaults `status=ACTIVE`; `set_status` round-trips in memory; migration `0034` upgrade→downgrade→upgrade against `pg_engine` and asserts the column exists.
2. Implement model + stores + migration.
3. Failing API tests (in `tests/test_tenant_scoping_isolation.py` or a focused module): with `auth.enabled=False`, `GET /runs` with `X-Hiveplane-Tenant: acme` behaves as before; with `auth.enabled=True` + a key for tenant `acme`, a mismatched header `beta` yields 403; a `TenantScopeError` raised by a store surfaces as 403 (not 500), and `TenantNotFoundError` as 404.
4. Implement `resolve_tenant`/`get_tenant_context` + error handlers.
5. Run the exit gate.

**Acceptance:** The boundary reconciles identity/header when auth is enabled, preserves permissive behavior when disabled, and tenancy errors render 403/404.

---

### Task 2: Tenant admin operations — create/suspend/reinstate/quota (M58-06)

**Work items:** M58-06.

**Files:**
- Create: `src/hiveplane/tenancy/admin.py`, `src/hiveplane/api/tenants.py`
- Modify: `src/hiveplane/tenancy/models.py` (`TenantQuota`), `src/hiveplane/tenancy/store.py` (quota in payload), `src/hiveplane/api/deps.py`, `src/hiveplane/api/app.py`, `src/hiveplane/execution/service.py` (`submit` suspension check), `src/hiveplane/delivery/service.py` (delivery suspension check)
- Test: `tests/test_tenancy_admin.py`, `tests/test_api_tenants.py`

**Interfaces:**
- `TenantQuota(max_agents: int | None = None, max_monthly_usd: float | None = None, max_concurrent_runs: int | None = None)`; `Tenant` gains `quota: TenantQuota = TenantQuota()`.
- `TenantAdminService(store: TenantStore, *, clock=None)`:
  - `create_tenant(ctx, *, tenant_id, name, quota=None) -> Tenant` (system/ADMIN only; rejects existing id with `TenantAlreadyExistsError` — add to `tenancy/errors.py`).
  - `suspend_tenant(ctx, tenant_id, *, actor="operator") -> Tenant` / `reinstate_tenant(ctx, tenant_id, *, actor) -> Tenant` (system/ADMIN only).
  - `set_quota(ctx, tenant_id, quota) -> Tenant`; `require_active(ctx, tenant_id) -> Tenant` — raises `TenantSuspendedError` (add to `tenancy/errors.py`) when `status != ACTIVE`, `TenantNotFoundError` when absent; `SYSTEM` context bypasses.
  - `list_tenants(ctx)` delegates to the store.
- Suspension enforcement: `RunService.submit` calls `admin.require_active(ctx, ctx.tenant_id)` before admitting; `DeliveryService` (or its caller) skips delivery for a suspended destination tenant. Suspended tenant delivery/run → `TenantSuspendedError` → **403**.
- API (router `tenants`, tags `["tenants"]`):
  - `POST /tenants` body `{tenant_id, name, quota?}` → `Tenant` (ADMIN/system, `Permission.KEYS_MANAGE`).
  - `POST /tenants/{tenant_id}/suspend` / `.../reinstate` → `Tenant`.
  - `GET /tenants` → `list[Tenant]` (FLEET_READ).
  - `PUT /tenants/{tenant_id}/quota` body `TenantQuota` → `Tenant`.
  - Wire `app.state.tenant_admin_service` + `get_tenant_admin_service` dep; add `(TenantAlreadyExistsError, 409)` / `(TenantSuspendedError, 403)` handlers.

**Steps:**
1. Failing `tests/test_tenancy_admin.py`: create/suspend/reinstate/quota round-trip in memory (and Postgres); `require_active` blocks suspended and bypasses for system.
2. Implement model + service + store support.
3. Failing tests: submitting a run for a suspended tenant is refused (403) and delivery is suppressed; system context can still administer.
4. Wire service + endpoints + router; test `POST /tenants` then a run submit under it succeeds, after suspend it 403s.
5. Run the exit gate.

**Acceptance:** A tenant can be created, suspended, reinstated, and quota-assigned; a suspended tenant cannot submit runs or receive deliveries.

---

## Phase 2 — Store & identity isolation

### Task 3: Data-plane store isolation (M58-01)

**Work items:** M58-01 (secrets, artifacts, cost, delivery, events, corpus, worker) plus the certification write gap and MCP deny.

**Files:**
- Modify stores: `src/hiveplane/secrets/store.py`, `src/hiveplane/artifacts/store.py`, `src/hiveplane/cost/store.py`, `src/hiveplane/delivery/store.py`, `src/hiveplane/events/store.py`, `src/hiveplane/corpus/store.py`, `src/hiveplane/worker/store.py`, `src/hiveplane/certification/store.py`, `src/hiveplane/mcp/store.py`
- Modify services/callers to pass `ctx`: `secrets/service.py`, `artifacts/service.py`, `cost/service.py`, `delivery/service.py`, `events/service.py`, `corpus/service.py`, `worker/registry.py`, `certification/workflow.py`, and the API routers that call them (`api/identity.py`, `api/artifacts.py`, `api/cost.py`, `api/delivery.py`, `api/events.py`, `api/corpora.py`, `api/workers.py`, `api/certifications.py`, `api/mcp.py`)
- Test: extend `tests/test_tenant_scoping_isolation.py`; update the affected existing store/service/API tests.

**Contract for every store above:**
- Each read method gains `ctx: TenantContext = DEFAULT_CONTEXT` and returns `None`/`[]` (or filters) when `not ctx.scopes(tenant_id)`.
- Each write method gains `ctx` and calls `ctx.require(record.tenant_id)` (or the explicit `tenant_id` argument) before persisting; cross-tenant → `TenantScopeError`.
- `certification/store.py` `add(...)` gains `ctx` and refuses to overwrite/reuse a `record_id` owned by another tenant (in-memory and Postgres).
- `mcp/store.py` gains `ctx` and calls `require`/`scopes` (it is currently ctx-keyed but never denies).
- Services forward their own `ctx` parameter to stored calls; API routers pass the resolved request `ctx` (not a raw query/body `tenant_id`) — caller-supplied `tenant_id` params are removed in Task 7, but here the *service* seam becomes ctx-aware.

**Steps:**
1. Extend `tests/test_tenant_scoping_isolation.py` with one read-hides / write-denies pair per store (in-memory), plus a `pg_engine` section for at least the Postgres-backed stores.
2. Implement store `ctx` changes store-by-store; update callers and existing tests until green.
3. Add the certification cross-tenant-overwrite test and the MCP deny test.
4. Run the exit gate.

**Acceptance:** Every data-plane store enforces the M25 scoping contract; a v0.1.0/v0.2.0 in-memory or Postgres store cannot read or write another tenant's rows.

---

### Task 4: Per-tenant API keys, roles, and membership (M58-04)

**Work items:** M58-04.

**Files:**
- Modify: `src/hiveplane/auth/store.py`, `src/hiveplane/auth/keys.py`, `src/hiveplane/auth/service.py`, `src/hiveplane/api/identity.py`, `src/hiveplane/tenancy/store.py` (membership lookup)
- Test: `tests/test_m45.py` additions, `tests/test_tenant_scoping_isolation.py` additions

**Interfaces:**
- `AuthStore` methods gain `ctx: TenantContext = DEFAULT_CONTEXT`; reads filter by `ctx.scopes(tenant_id)`, writes `ctx.require`. `find_key_by_hash` stays global (authentication), but `get_key`/`revoke` are scoped.
- `ApiKeyService.create(ctx, tenant_id, role, ...)` requires `ctx.require(tenant_id)`; `revoke(ctx, key_id)` raises `AuthenticationError`/returns 404 when the key belongs to another tenant; `list_keys(ctx, tenant_id)` scopes.
- `AuthService.login(...)` resolves the role from a `Membership` when one exists for `(tenant_id, operator_id)` instead of trusting the request body role; if no membership and auth is enabled, default to `VIEWER`. (Demo path when auth disabled may still accept the supplied role — document it.)
- `require_active` (Task 2) is consulted by `authenticate_key`/`login` so a suspended tenant's keys are refused (403).
- `api/identity.py`: `DELETE /keys/{key_id}` passes `principal` as ctx so a cross-tenant revoke 404s; `GET /keys`, access audit are scoped to the principal's tenant.
- Membership-aware `authorize`: `AuthService.authorize` may consult `Membership` for the operator's effective role (fall back to the identity role if none).

**Steps:**
1. Failing tests: two tenants' keys are invisible to each other (`list_keys`, `revoke` cross-tenant → error); `login` for a member resolves the membership role; suspended tenant's key is refused.
2. Implement store/service/router changes; update `tests/test_m45.py`.
3. Run the exit gate.

**Acceptance:** API keys, roles, and memberships are per-tenant; a key cannot read or revoke another tenant's keys; a suspended tenant cannot authenticate.

---

## Phase 3 — Per-tenant budgets & policy

### Task 5: Per-tenant budgets + spend caps (M58-02)

**Work items:** M58-02.

**Files:**
- Modify: `src/hiveplane/budget/service.py`, `src/hiveplane/budget/models.py` (`CostAttribution` already tenant-aware — ensure it is set), `src/hiveplane/cost/service.py`, `src/hiveplane/cost/depth.py` (`evaluate_admission`), `src/hiveplane/execution/service.py` (`record_usage`, `submit`), `src/hiveplane/execution/admission.py`/`gates.py` (thread ctx), `src/hiveplane/api/app.py` (wire `CostService` into `BudgetService`)
- Test: `tests/test_execution_budget.py`, `tests/test_m49.py`, `tests/test_tenant_scoping_isolation.py` additions

**Interfaces:**
- `BudgetService.check(workload, context, *, ctx: TenantContext = DEFAULT_CONTEXT)` and `record_usage(workload, report, *, ctx)` and `snapshot(workload, run_id, *, ctx)` — every store call passes `ctx`; `CostAttribution(tenant_id=ctx.tenant_id, ...)`.
- `BudgetService` gains an optional `cost_service: CostService | None = None`. On `record_usage`, after pricing, construct a `CostEvent(event_id, tenant_id=ctx.tenant_id, team_id=..., workload_id=workload.name, run_id=report.run_id, cost_type=CostType.MODEL, model=model_identity, cost_usd=cost, completed=False, retry=False, escalation=False, occurred_at=report.timestamp)` and call `cost_service.record(event)` so M49 periods/caps and M50 showback/ROI become live.
- `BudgetService.check` additionally calls `cost_service.check_cap(ctx.tenant_id, CostPeriodKind.DAY, projected_usd=0.0)` and converts `SpendCapExceededError` into `BudgetCheck(allowed=False, level=..., reason="tenant spend cap exceeded", ...)` (fail-closed).
- `RunService.record_usage` passes `ctx=run_ctx` to `self._budget.record_usage(...)`; `AdmissionPipeline` threads the submission's `TenantContext` into `BudgetService.check`.
- `evaluate_admission` (`cost/depth.py`) is called from admission (or its gate) so enforced tenant caps hard-stop runs.

**Steps:**
1. Failing tests: a run's usage under tenant `acme` is attributed to `acme` (not `default`) in both budget and cost stores; a tenant-level enforced `cap_usd` blocks a run; `CostService.record` receives an event so `showback` is non-empty.
2. Implement service/execution wiring; update existing budget tests to pass ctx where needed.
3. Add a Postgres budget-store tenant test.
4. Run the exit gate.

**Acceptance:** Budget accrual and spend caps are per tenant; a real run under a non-default tenant is metered and can be hard-stopped by that tenant's cap.

---

### Task 6: Per-tenant policy packs + defaults (M58-03)

**Work items:** M58-03.

**Files:**
- Modify: `src/hiveplane/policy/packs.py`, `src/hiveplane/policy/engine.py`, `src/hiveplane/policy/pack_registry.py`, `src/hiveplane/core/decision.py` (`PolicyContext.tenant_id`), `src/hiveplane/execution/admission.py`, `src/hiveplane/execution/tools.py`, `src/hiveplane/api/policy.py`, `src/hiveplane/api/app.py`
- Test: `tests/test_policy_packs.py`, `tests/test_policy_pack_registry.py`, `tests/test_policy_m40.py`, `tests/test_policy_api.py`, `tests/test_tenant_scoping_isolation.py` additions

**Interfaces:**
- `InMemoryPolicyPackStore._packs` keyed by `(tenant_id, name)`; `save` duplicate check is per tenant; `apply`'s pinned `{team}:{name}` copies are tenant-keyed. `get`/`list_packs`/`for_team` unchanged in signature but correctly per tenant.
- `PolicyContext` gains `tenant_id: str = DEFAULT_TENANT_ID`.
- `PolicyEngine.evaluate(context, *, dry_run=False, ctx: TenantContext = DEFAULT_CONTEXT)` (or derive `ctx` from `context.tenant_id`); `_pack_decision` calls `self.packs.for_team(context.team, ctx=ctx)`.
- Per-tenant defaults: `PolicyPackRegistry` gains `set_default(ctx, pack) -> PolicyPack` / `default_for(ctx) -> PolicyPack | None` (or a `default` flag on `PolicyPackMetadata`); `PolicyEngine` applies the tenant default pack when no team pack matches. Precedence documented in the design doc.
- Callers pass tenant: `AdmissionPipeline` builds `PolicyContext(..., tenant_id=run.tenant_id)`; `ToolGateway` similarly; `POST /policy/evaluate` gains a `TenantDep` and sets `tenant_id` from it.

**Steps:**
1. Failing tests: two tenants may both own a pack named `platform-pack`; the engine applies tenant A's pack and not B's; a tenant default pack applies when no team pack matches.
2. Implement store re-keying + engine + context + registry defaults; update existing policy tests to be explicit about tenant.
3. Wire callers + router; run exit gate.

**Acceptance:** Policy packs and defaults are isolated per tenant; evaluation uses the acting run/tenant's packs; no cross-tenant name collision.

---

## Phase 4 — Surface scoping & adversarial proof

### Task 7: Router + CLI tenant scoping (M58-05)

**Work items:** M58-05.

**Files:**
- Modify routers: `api/registry.py`, `api/certifications.py`, `api/learning.py`, `api/mcp.py`, `api/progressive.py`, `api/health.py`, `api/cost.py`, `api/workers.py`, `api/delivery.py`, `api/scheduler.py`, `api/artifacts.py`, `api/corpora.py`, `api/events.py`, `api/approvals.py`, `api/policy.py`
- Modify services called by those routers to accept `ctx` (registry, certification workflow, learning, mcp, progressive, health, worker, scheduler, artifacts, corpus, events).
- Modify: `src/hiveplane/cli.py` (global `--tenant`/`--team`, send headers on every request, map 403/404 to a CLI error).
- Test: new `tests/test_m58_api_isolation.py` (or extend `tests/test_api_*`); `tests/test_cli*.py` additions.

**Contract:**
- Every router resolves `ctx = Depends(get_tenant_context)` and passes it to its service; no router may read `tenant_id` from a query/body for isolation decisions. Remove/ignore caller-supplied `tenant_id` on `cost`, `workers`, `delivery`, `scheduler`.
- Every mutating endpoint gates on an appropriate permission (registry/certifications/learning/mcp/progressive/health/scheduler currently have none — add `FLEET_READ` for reads and the relevant `*_MANAGE`/write permission for mutations, reusing existing permissions; do not invent new ones beyond what the design doc lists).
- CLI: add top-level `--tenant` and `--team` options; `_request` and `_post_workload` always send `X-Hiveplane-Tenant`/`X-Hiveplane-Team` when provided; a 403/404 becomes a clear `click` error. No behavior change when the flags are absent.
- UI needs no change (it already forwards the header); note this in the design doc.

**Steps:**
1. Failing tests: each previously-unscoped router returns only the acting tenant's data and 404s for another tenant; caller-supplied `tenant_id` is ignored; CLI `--tenant` produces the header (assert via a fake transport) and maps 403/404.
2. Implement router/service ctx threading and permission gates; update existing API tests.
3. Implement CLI options; run exit gate.

**Acceptance:** Every API path is tenant-scoped and permission-gated; the CLI can act as a tenant; no path lets a caller choose another tenant's data.

---

### Task 8: Adversarial isolation suite (M58-07, M58-08)

**Work items:** M58-07, M58-08.

**Files:**
- Create: `tests/test_m58_isolation.py`
- Modify: `tests/test_tenant_scoping_isolation.py` only if consolidation is cleaner.

**Required coverage (one test per bullet, all passing):**
- **Store matrix:** for every store in the tenant-scoping inventory, a read outside the acting tenant returns nothing and a cross-tenant write raises `TenantScopeError` (in-memory and, where a Postgres store exists, via `pg_engine`).
- **Budget/policy/key isolation:** tenant A's budget, caps, policy packs/defaults, and API keys are invisible to and unusable by tenant B.
- **Suspended tenant:** cannot submit a run, cannot authenticate a key, does not receive deliveries, returns 403.
- **Cross-tenant HTTP:** for representative endpoints across every router, tenant B receives 404 (read) / 403 (write) for tenant A's resource id; never a 200 and never a 500.
- **No existence leak:** error bodies/status do not reveal the other tenant's resource.
- **Determinism:** running the matrix twice yields identical results.
- **Field-test tie-in:** name the release-gate scenario this suite protects in a module docstring.

**Steps:**
1. Write the suite against the implemented behavior; every case must pass.
2. Fix any leak found (a leak is a release blocker — route it through the task fix loop, do not weaken the test).
3. Run the exit gate.

**Acceptance:** The adversarial suite proves cross-tenant read/write denial, suspended-tenant denial, and budget/policy/key isolation end-to-end.

---

### Task 9: M58 acceptance pass, docs & changelog (#457)

**Work items:** M58-08 evidence, #457.

**Files:**
- Create: `tests/test_m58_acceptance.py`
- Modify: `docs/design/reporting-tenancy-distribution-design.md` (status → implemented + decisions), `docs/wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md` (check M58 boxes + exit gate), `docs/USER_GUIDE.md` (tenant admin, isolation, budgets/policy/keys), `CHANGELOG.md`

**Steps:**
1. Write `tests/test_m58_acceptance.py` covering the five M58 acceptance criteria end-to-end through `create_app()`: (a) a tenant sees/acts only on its own resources; (b) per-tenant budgets/policies/keys isolated and enforced; (c) a suspended tenant cannot submit or receive delivery; (d) cross-tenant read/write returns 403/404 consistently; (e) tenant admin creates/suspends and assigns quota.
2. Update the design doc: record decisions (auth-bound header; tenant-keyed in-memory policy packs — Postgres deferred; global exceptions: audit chain/transparency/kill switch/incident/HA; suspension enforcement points) and set status.
3. Add USER_GUIDE sections and a CHANGELOG `### Added (M58 — Multi-Tenant Isolation)` entry ending with the exact test/coverage/lint line.
4. Run the full gate: `python -m pytest`, `python -m pytest --cov=src/hiveplane --cov-report=term-missing`, `python -m ruff check`, `python -m mypy src/ tests/`; confirm coverage >95%.
5. Close issues #449–#457 and mark the M58/Part-17 exit gate.

**Acceptance:** All M58 acceptance criteria are proven by tests; docs and changelog updated; exit gate green.

---

## See Also

- [D38 design](../design/reporting-tenancy-distribution-design.md)
- [WBS v0.2.0 Part 17](../wbs/v0.2.0/wbs-v0.2.0-part17-reporting-tenancy.md)
- [M25-01 plan](m25-01-tenancy-scoping.md) — scoping conventions this plan follows
- [M57 plan](m57-reporting-retention.md) — predecessor milestone
