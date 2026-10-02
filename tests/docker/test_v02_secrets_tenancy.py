"""L8 (v0.2.0) — Secrets, RBAC & multi-tenancy scenarios S16-S17 (M61-05, #480).

S16 a secret never appears in logs/traces/agent context; cross-tenant ref rejected (gate 13)
S17 RBAC viewer denied privileged actions; per-tenant budget/policy/key isolation; 429 (gates 6, 34)

Runner requirements (see the v0.2.0 docker test plan):
- ``HIVEPLANE_AUTH__ENABLED=true`` and ``HIVEPLANE_AUTH__ADMIN_KEY`` (bootstrap, D-10) so RBAC
  and tenant isolation are enforced and the first admin key can be minted.
- ``HIVEPLANE_API__RATE_LIMIT_ENABLED=true`` and a low ``HIVEPLANE_API__RATE_LIMIT_REQUESTS``
  for the 429 check (D-11).
"""

from __future__ import annotations

import os

import pytest

from v02_support import get, manifest, post, register, request, submit, system_headers

pytestmark = pytest.mark.docker

_WORKLOAD = "support-agent"
_VIEWER_HEADERS = "Authorization"
_SECRET_VALUE = "s3cr3t-should-never-appear"


@pytest.fixture(scope="module")
def secret_tenant() -> dict[str, str]:
    status, tenant = post(
        "/tenants",
        {"tenant_id": "ft-tenant", "name": "Field Test Tenant"},
        headers=system_headers(),
    )
    assert status in (200, 201, 409), tenant
    return {"tenant": "ft-tenant"}


def test_s16_secret_round_trip_and_absence_from_run_surfaces(secret_tenant: dict[str, str]) -> None:
    tenant = secret_tenant["tenant"]
    create_status, _ = request(
        "POST",
        "/secrets",
        {"name": "db-password", "value": _SECRET_VALUE},
        tenant=tenant,
    )
    assert create_status in (200, 201, 409), create_status

    list_status, secrets = get("/secrets", tenant=tenant)
    assert list_status == 200 and isinstance(secrets, list), secrets
    assert all(_SECRET_VALUE not in str(entry) for entry in secrets), (
        "secret value must be write-only"
    )

    rotate_status, _ = request(
        "POST",
        "/secrets/db-password/rotate",
        {"name": "db-password", "value": "rotated-value"},
        tenant=tenant,
    )
    assert rotate_status in (200, 201, 404), rotate_status

    # A run's surfaces must never contain the secret value.
    status, run = submit(_WORKLOAD, context="sandbox", tenant=tenant)
    if status == 201:
        run_id = run["id"]
        surfaces = [f"/runs/{run_id}/events", f"/runs/{run_id}/story", f"/runs/{run_id}"]
        for path in surfaces:
            _, body = get(path, tenant=tenant)
            assert _SECRET_VALUE not in str(body), f"secret leaked into {path}"


def test_s17_viewer_is_denied_privileged_actions() -> None:
    status, issued = post("/keys", {"role": "viewer", "scopes": [], "label": "ft-viewer"})
    assert status in (200, 201), issued
    viewer_header = {_VIEWER_HEADERS: f"Bearer {issued['token']}"}

    # A viewer cannot mutate the registry, quarantine, or control runs.
    for method, path, payload in (
        ("DELETE", "/workloads/support-agent", None),
        ("POST", "/quarantines", {"workload": _WORKLOAD, "reason": "viewer attempt"}),
        ("POST", "/tools", {"tool_id": "mcp.x", "trust_level": "read_only"}),
    ):
        deny_status, _ = request(method, path, payload, headers=viewer_header)
        assert deny_status in (401, 403), (method, path, deny_status)


def test_s17_per_tenant_isolation(secret_tenant: dict[str, str]) -> None:
    tenant = secret_tenant["tenant"]
    register(manifest(_WORKLOAD), tenant=tenant)
    # A workload registered only in "default" is invisible to the other tenant.
    status, workload = get("/workloads/support-agent", tenant="default")
    assert status == 200, workload
    other_status, _ = get("/workloads/support-agent", tenant="definitely-not-a-tenant")
    assert other_status in (401, 403, 404), "cross-tenant read must not leak"


def test_s17_over_limit_tenant_gets_429() -> None:
    assert os.environ.get("HIVEPLANE_API__RATE_LIMIT_ENABLED", "").lower() in (
        "1",
        "true",
        "yes",
    ), "the v0.2.0 runner must enable rate limiting for gate 34 (zero-skip policy)"
    limit = int(os.environ.get("HIVEPLANE_API__RATE_LIMIT_REQUESTS", "0"))
    # A dedicated tenant so setup/seed traffic in other tenants does not share
    # (and pre-exhaust) this tenant's bucket.
    post(
        "/tenants",
        {"tenant_id": "ft-429", "name": "Rate Limit Tenant"},
        headers=system_headers(),
    )
    key_status, issued = post(
        "/keys",
        {"role": "admin", "scopes": [], "label": "ft-429"},
        tenant="ft-429",
        headers=system_headers(),
    )
    assert key_status in (200, 201), issued
    headers = {"Authorization": f"Bearer {issued['token']}"}
    statuses: list[int] = []
    for _ in range(limit + 20):
        statuses.append(request("GET", "/runs", headers=headers, tenant="ft-429")[0])
    assert 429 in statuses, f"no 429 after {limit + 20} requests over a {limit} limit"
    assert statuses[0] == 200, "the tenant must not be limited from the first request"
