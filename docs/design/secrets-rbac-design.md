# D33: Secrets, RBAC & Identity Design

> Status: implemented (M45)

**Milestones:** M45 · **Extends:** —

## Problem

The plane has no secret store and no operator identity. Credentials live in workload config or environment files, agents can pull them into context, there is no rotation story, and every API caller is effectively an admin. Secret leakage is the highest-severity risk in v0.2.0, and an approval gate enforced only in the UI is not a gate at all.

D33 adds a per-tenant encrypted secret store injected at the execution boundary, rotation without redeploy, RBAC-lite with scoped API keys and UI login, and an access audit. All authorization is enforced server-side; the UI is never the only gate.

## Overview

```
 operators ─▶ API key/session ─▶ Authn ─▶ RBAC(tenant+role) ─▶ server-side gate
                                                              approve|promote|kill
 ┌ Secret Store (per tenant) ┐ encrypted at rest (managed key)
 │  versioned secret refs    │
 └──────────┬────────────────┘
            │ resolve at boundary (never in context)
            ▼
 Execution Boundary: env/tmpfs mount ─▶ sandbox · redaction on logs/traces/audit
```

## Design

### Encrypted Secret Store

Secrets are stored per tenant, encrypted at rest with a managed key (envelope encryption: data keys wrapped by a KMS/key-provider key). The store holds ciphertext only; plaintext exists transiently at the execution boundary.

```yaml
spec:
  secrets:
    - name: pg-readonly
      ref: secret://tenant-a/pg-readonly@3
      inject: { as: file, path: /run/secrets/pg-readonly, mode: "0400" }
    - name: api-token
      ref: secret://tenant-a/api-token@1
      inject: { as: env, name: PROVIDER_TOKEN }
```

A **secret ref** is `secret://<tenant>/<name>@<version>`. Workloads reference refs, never values. The store API returns metadata (name, version, timestamps, consumers) but never plaintext to operators or the UI.

### Runtime Injection

Secrets are resolved **at the execution boundary**, not at admission or in the agent process:

1. The adapter requests the secret by ref for its run.
2. The boundary authorizes the request (workload allow-list + tenant + role) and decrypts.
3. The value is injected as an environment variable or tmpfs file inside the sandbox (D11), never persisted to disk.
4. The value is registered with the redactor for the run's lifetime.

No secret is ever placed in agent context, tool arguments (except where a tool explicitly requires it at the boundary), logs, traces, span attributes, error messages, or audit events. This is enforced by a redaction layer plus adversarial **absence tests**: every sink (context, logs, traces, audit, fan-out, artifacts) is asserted to not contain a canary secret.

### Rotation

Rotation is versioned and takes effect on the **next run** without redeploying workloads:

- `hiveplane secrets rotate <name>` creates a new version and makes it current.
- Workloads pinned to `@N` keep that version; workloads on `@latest` pick up the new version at next run.
- In-flight runs keep the version they resolved; no mid-run swap.
- Old versions are retained for a grace period for rollback, then revoked.

### RBAC-lite

| Role | Approve / reject | Promote / certify | Kill switch | Secrets / keys | Read fleet |
|------|------------------|-------------------|-------------|----------------|-----------|
| `admin` | yes | yes | yes | manage | yes |
| `approver` | yes | no | no | no | yes |
| `viewer` | no | no | no | no | yes |

Membership is per tenant. Operators authenticate via UI login (session) or **scoped API keys**; a key carries tenant + role + optional narrower scope (e.g., read-only). Scopes are additive to the role, never wider: a scoped key cannot exceed its scope even if the role would allow more.

### Server-Side Enforcement

Every privileged action — approve, reject, promote, certify, kill switch, secret/key management — is authorized **server-side** at the API/handler before any state change. The UI hides controls for usability, but hiding is not enforcement: a direct API call with a viewer key returns `403`. Tests call the API directly with insufficient roles to prove it.

### Access Audit

- **Operator login history** — actor, method (session/key), tenant, IP, timestamp, result.
- **API-key usage analytics** — key id, scope, request counts, last used, denied attempts.
- **Secret access** — which workload/run resolved which ref version and when (not the value).

All events are append-only and attributable (DD-07).

### CLI / API

```
hiveplane secrets put <name> --from-env VAR
hiveplane secrets rotate <name>
hiveplane secrets list | show <name>      # metadata only
hiveplane keys create --role approver --scope runs:read
hiveplane keys revoke <key_id>
hiveplane auth whoami
```

```
POST   /secrets                 # create (value write-only)
POST   /secrets/{name}/rotate
GET    /secrets/{name}          # metadata only
POST   /keys
DELETE /keys/{id}
GET    /audit/access?tenant=...
```

## Data Model

```
secrets(id PK, tenant_id, name, current_version, created_at, rotated_at)
  UNIQUE (tenant_id, name)
secret_versions(secret_id FK, version, ciphertext BYTEA, key_id,
  created_at, revoked_at)  PRIMARY KEY (secret_id, version)
api_keys(id PK, tenant_id, role, scopes TEXT[], hashed_key,
  created_at, last_used_at, revoked_at)
access_audit(id PK, tenant_id, actor, method, action, target, result, created_at)
```

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Key provider unavailable | secret resolution fails-closed; run pauses, never proceeds without its secret |
| Secret ref missing/revoked | run fails admission with a reason; no fallback to an old value |
| Redactor failure | run fails-closed; logs are not emitted without redaction |
| Unknown/expired API key | `401`; no implicit anonymous access |
| Role check error | deny by default (`403`) |

## Security

- Encrypted at rest with envelope encryption; plaintext only at the boundary.
- Secrets never enter context, logs, traces, audit, fan-out, or artifacts — asserted by absence tests.
- Authorization is server-side; the UI is never the only gate.
- Every deny and every privileged action is audited (DD-07).
- Fail-closed on any resolution, redaction, or authorization error.

## Testing

- A canary secret never appears in logs, traces, or agent context (adversarial absence test across every sink).
- Rotating a secret takes effect for the next run without redeploy; pinned versions are unaffected.
- A viewer cannot approve, promote, or trigger the kill switch (`403` from a direct API call).
- A scoped API key cannot exceed its scope.
- Login history and API-key usage are recorded and queryable.
- Key-provider failure pauses the run rather than leaking or proceeding.

## Open Questions

- Which KMS backends ship first (local file key, cloud KMS, Vault)?
- Should the approver role be scoped per workload/team, or tenant-wide?
- How long are revoked secret versions retained before hard deletion?

## See Also

- [Execution Sandbox Design](execution-sandbox-design.md) (D11) — tmpfs/env injection boundary
- [Defense & Policy v2 Design](defense-policy-v2-design.md) (D29) — kill switch and policy decisions
- [MCP Registry v2 Design](mcp-registry-v2-design.md) (D32) — tool credentials as secret refs
- [Operator UI Design](operator-ui-design.md) (D9) — login, roles, approval queue
- [PRD 05: Features](../prd/05-features.md) — Secrets & Identity
- [WBS Part 11](../wbs/v0.2.0/wbs-v0.2.0-part11-secrets-workers.md) — M45
