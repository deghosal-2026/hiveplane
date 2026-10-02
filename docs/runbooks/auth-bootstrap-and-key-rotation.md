# Runbook — Auth bootstrap & key rotation

How to enable operator authn/authz on HivePlane, mint the first key, and rotate/revoke keys.

## Background

When auth is enabled, every operator endpoint requires an API key, and key management
(`POST /keys`, `DELETE /keys/{key_id}`) itself requires the `admin` role. Without a way to obtain
the first key, RBAC is unreachable end-to-end (this was field-test defect **D-10**). HivePlane
therefore seeds a **bootstrap admin key** from configuration on startup.

Relevant settings (`AuthSettings`):

| Env | Default | Meaning |
|---|---|---|
| `HIVEPLANE_AUTH__ENABLED` | `false` | Require a bearer API key on operator endpoints. |
| `HIVEPLANE_AUTH__ADMIN_KEY` | unset | Known admin token seeded on startup so the first key can be minted over HTTP. Empty disables it. |

On startup, if auth is enabled and `ADMIN_KEY` is set, the service idempotently seeds it as an
admin key for the `default` tenant:

```python
# src/hiveplane/api/app.py
app.state.auth_service.keys.ensure_token(
    settings.auth.admin_key, "default", role=Role.ADMIN, label="field-test-bootstrap"
)
```

`ensure_token` is idempotent (it hashes the token and skips if already present), so restarts do
not create duplicates.

## Dev / field-test bootstrap

1. Set the bootstrap key (the field-test runner defaults it to `hp-field-test-bootstrap-admin`):

   ```sh
   export HIVEPLANE_AUTH__ENABLED=true
   export HIVEPLANE_AUTH__ADMIN_KEY="$(openssl rand -hex 32)"
   ```

2. Mint the first operator key with the bootstrap key:

   ```sh
   curl -sS -X POST "$HIVEPLANE_API/keys" \
     -H "Authorization: Bearer $HIVEPLANE_AUTH__ADMIN_KEY" \
     -H 'Content-Type: application/json' \
     -d '{"tenant_id":"default","role":"admin","label":"ops-1"}'
   # -> {"key_id":"key-…","token":"hpk-…"}   (token shown once)
   ```

3. **Remove the bootstrap key** (`unset HIVEPLANE_AUTH__ADMIN_KEY` or clear it in the deployment)
   and restart, so the long-lived shared secret is gone. The key you minted in step 2 remains.

## Production guidance

The env bootstrap key is **dev/field-test grade** — a single static secret. For production,
prefer one of:

- **Sealed / short-lived bootstrap**: inject `ADMIN_KEY` only for the first boot, mint an operator
  key, then rotate the secret away (steps 1–3 above, automated). Do not leave it in the manifest.
- **External IdP / SSO**: front operator auth with an IdP and map identities to roles, so no
  static admin secret is stored by the plane.
- **One-time in-process CLI**: run a short-lived command against the same store to seed the first
  key without exposing an HTTP bootstrap path. (Not yet shipped — tracked with M61-19.)

Never commit `ADMIN_KEY`; treat it like any root credential.

## Key rotation & revocation

- **Mint** a replacement with `POST /keys` (above); issue it before retiring the old one.
- **Revoke** with `DELETE /keys/{key_id}`. Revocation is checked on every authenticated request
  (`authenticate` fails closed on an unknown or revoked key), so a revoked key stops working
  immediately.
- **Rotate the signing / secret master key** independently via `POST /secrets/{name}/rotate`
  (secret store) — see `docs/design/` for the encrypted-secrets model.

## Evidence hygiene

The bootstrap admin key is transmitted only as an `Authorization: Bearer …` **header**. The
field-test runner logs request/response **bodies** and the path (not headers), so the key never
appears in `field_test/v0.2.0/results/**` evidence. Do not add header capture without redacting
`Authorization`.

## References

- `src/hiveplane/api/app.py` (startup seeding), `src/hiveplane/auth/keys.py` (`ensure_token`,
  `authenticate`, `revoke`), `src/hiveplane/config.py` (`AuthSettings`).
- `docs/field-test/v0.2.0/FIELD_TEST_REPORT.md` §3 (D-10), §6.8, §15.
- Field-test auth pass: `scripts/field-test-v02.sh --auth`.
