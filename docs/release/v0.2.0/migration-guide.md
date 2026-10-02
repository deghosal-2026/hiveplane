# Migrating HivePlane v0.1.0 → v0.2.0

**Audience:** operators running a HivePlane control plane and workload authors maintaining
agent manifests, corpora, or custom runtime adapters.

v0.2.0 is the Complete Fleet OS: the fleet runs itself (triggers, pipelines, GitOps
reconciliation), defends itself (drift, injection, policy, kill switches), explains itself
(cost/ROI, reporting), and scales itself (tenancy, distributed workers, Helm). Most of the
release is additive, but **the storage schema, the runtime-adapter contract, and the
default authorization model change in ways that require action**. This guide lists every
breaking change and the exact step to take.

> **Upgrade path:** v0.1.0 → v0.2.0 is a single-hop upgrade. Migration `0003` is
> forward-only and cannot be rolled back in place — take a backup first.

---

## 1. Compatibility summary

| Area | Breaking? | Action required |
|------|-----------|-----------------|
| Python package | No | `pip install --upgrade hiveplane==0.2.0` |
| Storage schema | **Yes** | Back up, then run migrations through `0036` |
| Runtime adapter contract | **Yes** | Custom adapters must implement contract v2 |
| Authorization / tenancy | **Yes** (when auth is enabled) | Scope credentials per tenant; set tenant headers |
| REST API | Mostly additive | Existing endpoints keep working; error shape gains `error` |
| Manifest schema | Additive | Existing manifests validate unchanged |
| CLI | Additive | New global `--tenant/--team` flags; new commands |
| Configuration | Additive | New env sections; safe defaults unchanged |

---

## 2. Upgrade procedure

### 2.1 Back up first

Migration `0003_tenancy_scoping` is **forward-only**: it seeds the `default`/`system`
tenants, backfills every legacy row to `default`, **deletes orphan runs and dangling
workload references**, then enforces non-null `tenant_id` and composite foreign keys. Take
a database snapshot (and keep your `.hiveplane/` data directory if you use the JSON store)
before upgrading so you can restore rather than reverse the migration.

```bash
# PostgreSQL
pg_dump -Fc hiveplane > hiveplane-pre-v0.2.0.dump

# JSON store (default local mode)
cp -R .hiveplane .hiveplane.pre-v0.2.0
```

You can also use the built-in backup service (introduced in M59):

```bash
hiveplane backup create --out hiveplane-pre-v0.2.0.bak
hiveplane backup verify hiveplane-pre-v0.2.0.bak
```

### 2.2 Upgrade the package and stack

```bash
pip install --upgrade hiveplane==0.2.0

# Docker Compose reference stack
git pull
docker compose pull
docker compose up -d
```

### 2.3 Run migrations

Auto-migration on startup is preserved, so a normal `docker compose up` applies migrations
`0003`–`0036`. To run them explicitly instead:

```bash
alembic upgrade head
alembic current   # expect 0036
```

Migrations `0003`–`0036` are forward-only and idempotent. Checkpoint `0034` adds tenant
lifecycle state; `0035` adds replay records; `0036` adds single-use approval tokens.

### 2.4 Verify

```bash
pytest                                   # full suite
hiveplane health list                    # control plane answers
hiveplane cluster leader                 # confirms leader election (HA)
hiveplane verify <attestation-id>        # old attestations still verify
```

---

## 3. Breaking changes

### 3.1 Storage schema is now tenant-scoped

Every durable table gains a non-null `tenant_id` (and `team_id`/`attribution_key` on
run/usage/cost tables). The reserved ids are:

- `default` — where all v0.1.0 rows are backfilled.
- `system` — plane-internal operations.

**What to do:**

- Expect one-time data changes from `0003`: orphaned runs and dangling workload
  references are removed (they could not be preserved under the new foreign keys).
- If you built anything directly against the tables, add `tenant_id` to reads/writes.
  Reads outside the acting tenant now look like the record does not exist, and writes
  that cross a tenant boundary raise `TenantScopeError`.
- Existing data is not lost — it simply lives under the `default` tenant.

### 3.2 Runtime adapter contract v2

The adapter boundary is explicitly versioned. `CONTRACT_VERSION` is now `2`, and the
`Adapter` interface gains required methods:

```python
def capabilities(self) -> AdapterCapabilities: ...
def stream(self, ...) -> Iterator[AdapterEvent]: ...
def model_identity(self) -> str | None: ...
def conformance_version(self) -> int: ...
```

**What to do:**

- **Custom adapters** must implement contract v2 and pass the conformance suite
  (`tests/conformance.py`). Built-in adapters (raw-worker, LangGraph) are already v2.
- The `*_of` helper functions tolerate pre-v2 adapters, so an adapter you cannot update
  immediately may still load where a helper is used — but it will not report capabilities,
  an ordered event stream, or an inference-captured model identity.
- Manifests may pin the expected contract with `spec.runtime.adapter_contract` (default
  `2`). Leave it unset to accept v2.
- New reference adapters (PydanticAI, OpenAI Agents SDK) are available via the
  `hiveplane[pydantic-ai]` and `hiveplane[openai-agents]` extras.

### 3.3 Authorization: tenant-scoped credentials and headers

v0.1.0 had no auth boundary; v0.2.0 adds RBAC-lite, scoped API keys, encrypted secrets,
and per-tenant stores (M45, M58). With `HIVEPLANE_AUTH__ENABLED=true`:

- `get_tenant_context` is the single place a request becomes a `TenantContext`. The
  authenticated principal's tenant **must match** the `X-Hiveplane-Tenant` header; only a
  system principal may select an arbitrary tenant.
- API keys are resolved from the caller's tenant membership; the tenant store, key
  list/revoke, and login all resolve per tenant. A suspended tenant's keys are refused.
- `TenantScopeError` → `403`; unknown tenant/team → `404`.
- Secrets resolve at the execution boundary and are redacted from context, logs, traces,
  audit, fan-out, and artifacts.

**What to do:**

- If you stay on `HIVEPLANE_AUTH__ENABLED=false`, the `X-Hiveplane-Tenant` /
  `X-Hiveplane-Team` headers remain trusted local plumbing — this is **not** an auth
  boundary, so do not expose the control plane publicly.
- To enable auth, follow the user guide's auth bootstrap and key-rotation section:
  create an admin key, then issue per-tenant keys (`hiveplane keys create`). Every CLI
  call that should act in a tenant must pass `--tenant` (and optionally `--team`).
- Callers that previously passed a `tenant_id` **query/body parameter** to
  cost/workers/delivery/scheduler endpoints will have it ignored — the acting tenant comes
  from the header/credential instead.
- Re-check role permissions: reads require `FLEET_READ`; approve/promote/kill-switch and
  replay require their scoped permissions (e.g. `REPLAY_MANAGE`), and a viewer cannot
  approve.

### 3.4 Configuration

Configuration is additive; v0.1.0 defaults are preserved. New optional sections (all
disabled by default unless noted):

| Setting | Default | Purpose |
|---------|---------|---------|
| `HIVEPLANE_AUTH__ENABLED` | `false` | Turn on RBAC-lite and scoped keys |
| `HIVEPLANE_ROUTER__ENABLED` | `false` | Expose `POST /route` (smart task router) |
| `HIVEPLANE_A2A__ENABLED` | `false` | Expose A2A interop endpoints |
| `HIVEPLANE_FEDERATION__ENABLED` | `false` | Register/aggregate remote planes |
| `HIVEPLANE_ARTIFACTS__BACKEND` | `local` | `s3` selects the S3/MinIO backend |
| `HIVEPLANE_ARTIFACTS__*` | — | S3 endpoint/bucket/credentials |
| `HIVEPLANE_API__RATE_LIMIT_ENABLED` | `false` | Per-tenant token buckets (429 + `Retry-After`) |
| `HIVEPLANE_REPORTING__PII_ENABLED` | `false` | Scrub PII before the audit hash |
| `HIVEPLANE_DEFENSE__ENABLED` | — | Injection/egress defense (on by default where configured) |
| `HIVEPLANE_RECONCILE__ALLOW_DESTRUCTIVE` | `false` | Permit destructive GitOps actions |

**What to do:** review `.env.example` and add only the sections you need. If you set a
value inline today, the `HIVEPLANE_<SECTION>__<FIELD>` convention still applies.

### 3.5 REST API changes

Existing v0.1.0 endpoints are unchanged. The following are additive or broadening:

- **Error envelope** — responses now carry both the legacy `detail` field and a structured
  `error` object, plus an `X-Request-ID` correlation header:

  ```json
  {
    "detail": "workload not found",
    "error": {
      "code": 404,
      "message": "workload not found",
      "details": null,
      "request_id": "…"
    }
  }
  ```

  Clients that read `detail` keep working; clients MAY read `error.code`/`error.request_id`.

- **API v2 surface** — new `GET /v2/runs`, `GET /v2/version`, cursor pagination
  (`Page[T]`), and `X-Request-ID` echo. A Python SDK (`hiveplane.sdk.HivePlaneClient`) and
  agent-as-service (`POST /services/{workload}/invoke`) are available.
- **Status codes** — expect `403` for cross-tenant/wrong-role, `404` for
  tenant/team not found, and `429` (with `Retry-After`) when rate limits are enabled.
- **New auth endpoints** — `POST /auth/login`, `GET /auth/whoami`, `/keys`, `/secrets`,
  `/audit/access`.

### 3.6 CLI changes

- A global `--tenant/--team` option sends the tenant headers and maps `403`/`404` to a
  clear error. It is optional when auth is disabled.
- New command families: `triggers`, `pipelines`, `route`, `agents`, `reconcile`,
  `drift`, `quarantines`, `canary`, `experiment`, `policies`, `tools`, `secrets`, `keys`,
  `auth`, `health`, `probes`, `cost`, `workers`, `worker`, `queue`, `cluster`, `chaos`,
  `feedback`, `corpus`, `eval`, `shadow`, `delivery`, `artifacts`, `export`/`import`,
  `report`, `top`, `logs`, `replay`, `fleet`, `ask`, `backup`, `demo`.
- Existing commands (`init`, `validate`, `register`, `certify`, `certs`, `submit`, `runs`,
  `approvals`) keep their v0.1.0 arguments. `submit` accepts `--model-identity`, `--caller`,
  and `--context` as before.

### 3.7 Behavior changes to be aware of

These are not breaking for clients, but they change runtime semantics:

- **Production admission is stricter.** Promotion requires the current version to be
  `certified` with a valid, unexpired attestation for the *current artifact hash*. A
  manifest, toolset, model, or policy-version change automatically marks the workload
  `uncertified` and blocks promotion until re-certified.
- **Drift can auto-quarantine.** Certification expiry and drift thresholds can revoke
  production admission without an operator action; reinstatement requires a fresh passing
  certification.
- **Injection and egress defense run at the tool-call boundary** independent of
  `spec.output_shaping`. Malicious tool output is blocked before it reaches agent context,
  and egress is deny-by-default with cloud-metadata always blocked.
- **Budgets gained a context-token dimension, spend-velocity guard, and circuit breakers**
  that pause runs cleanly (with accounting).
- **Fan-out expanded from Slack/webhook to nine channels** with interactive/mobile
  approvals. Existing Slack/webhook configs continue to work.

---

## 4. Deprecations and removals

- Nothing was removed from the public CLI or API in v0.2.0.
- Deferred features from v0.1.0's "Known limitations" (multi-tenancy, ROI dashboards,
  Helm/cluster deployment, Homebrew, standalone binary, GitHub Action) now ship in
  v0.2.0 — see the release notes.

---

## 5. Rollback

Migration `0003` is forward-only, so rollback means restoring the v0.1.0 backup:

```bash
pg_restore -c -d hiveplane hiveplane-pre-v0.2.0.dump   # PostgreSQL
rm -rf .hiveplane && mv .hiveplane.pre-v0.2.0 .hiveplane  # JSON store
pip install --upgrade hiveplane==0.1.0
```

`hiveplane backup restore` can restore an archive created in step 2.1; it refuses a schema
mismatch or any integrity failure.

---

## 6. Getting help

- [CHANGELOG.md](../../../CHANGELOG.md) — full categorized history
- [User Guide](../../USER_GUIDE.md) — auth bootstrap, tenancy, and every subsystem
- [Release notes](release-notes.md) — highlights and field-test evidence
- [Security baseline](../../prd/06-security-baseline.md) · [SECURITY.md](../../../SECURITY.md)
