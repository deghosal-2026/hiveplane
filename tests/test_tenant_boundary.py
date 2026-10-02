"""Tenancy boundary: principal/header reconciliation and error mapping (M58-05)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.api.deps import TEAM_HEADER, TENANT_HEADER, resolve_tenant
from hiveplane.auth.models import AuthMethod, OperatorIdentity
from hiveplane.config import get_settings
from hiveplane.tenancy import Role
from hiveplane.tenancy.context import (
    DEFAULT_CONTEXT,
    SYSTEM_CONTEXT,
    SYSTEM_TENANT_ID,
    TenantContext,
)
from hiveplane.tenancy.errors import TenantNotFoundError, TenantScopeError


def _settings(enabled: bool) -> Any:
    return SimpleNamespace(auth=SimpleNamespace(enabled=enabled))


def _request(**headers: str) -> Any:
    return SimpleNamespace(headers=headers)


def _principal(tenant_id: str, role: Role) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="key-1",
        tenant_id=tenant_id,
        role=role,
        method=AuthMethod.API_KEY,
    )


def test_resolve_tenant_disabled_uses_header() -> None:
    ctx = resolve_tenant(
        _request(**{TENANT_HEADER: "acme", TEAM_HEADER: "platform"}),
        None,
        _settings(enabled=False),
    )
    assert ctx == TenantContext(tenant_id="acme", team_id="platform", role=Role.ADMIN)


def test_resolve_tenant_disabled_without_header_is_default() -> None:
    assert resolve_tenant(_request(), None, _settings(enabled=False)) == DEFAULT_CONTEXT


def test_resolve_tenant_matching_header_uses_principal() -> None:
    principal = _principal("acme", Role.VIEWER)
    ctx = resolve_tenant(
        _request(**{TENANT_HEADER: "acme", TEAM_HEADER: "platform"}),
        principal,
        _settings(enabled=True),
    )
    assert ctx.tenant_id == "acme"
    assert ctx.role == Role.VIEWER
    assert ctx.operator_id == principal.operator_id
    assert ctx.team_id == "platform"


def test_resolve_tenant_mismatched_header_is_403() -> None:
    with pytest.raises(HTTPException) as raised:
        resolve_tenant(
            _request(**{TENANT_HEADER: "beta"}),
            _principal("acme", Role.ADMIN),
            _settings(enabled=True),
        )
    assert raised.value.status_code == 403


def test_resolve_tenant_system_principal_selects_header() -> None:
    ctx = resolve_tenant(
        _request(**{TENANT_HEADER: "beta"}),
        _principal(SYSTEM_TENANT_ID, Role.ADMIN),
        _settings(enabled=True),
    )
    assert ctx.tenant_id == "beta"
    assert ctx.role == Role.ADMIN


def test_resolve_tenant_system_principal_without_header_is_system() -> None:
    principal = _principal(SYSTEM_TENANT_ID, Role.ADMIN)
    ctx = resolve_tenant(_request(), principal, _settings(enabled=True))
    assert ctx.tenant_id == SYSTEM_TENANT_ID
    assert ctx.operator_id == principal.operator_id
    assert ctx.is_system is True


def test_resolve_tenant_enabled_without_principal_is_401() -> None:
    with pytest.raises(HTTPException) as raised:
        resolve_tenant(_request(), None, _settings(enabled=True))
    assert raised.value.status_code == 401


def test_header_tenant_still_works_when_auth_disabled() -> None:
    client = TestClient(create_app())
    assert client.get("/runs", headers={TENANT_HEADER: "acme"}).status_code == 200
    assert client.get("/runs").status_code == 200


def _auth_client(
    monkeypatch: pytest.MonkeyPatch, tenant_id: str
) -> tuple[TestClient, str]:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    key = app.state.auth_service.keys.create(tenant_id, Role.ADMIN, ctx=SYSTEM_CONTEXT)
    return TestClient(app), key.token


def test_auth_enabled_allows_matching_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = _auth_client(monkeypatch, "acme")
    response = client.get(
        "/runs",
        headers={"Authorization": f"Bearer {token}", TENANT_HEADER: "acme"},
    )
    assert response.status_code == 200


def test_auth_enabled_rejects_mismatched_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = _auth_client(monkeypatch, "acme")
    response = client.get(
        "/runs",
        headers={"Authorization": f"Bearer {token}", TENANT_HEADER: "beta"},
    )
    assert response.status_code == 403


def test_tenant_scope_error_maps_to_403() -> None:
    app = create_app()

    @app.get("/_scope_error")
    def _raise_scope() -> None:
        raise TenantScopeError("acme", "crossed boundary")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/_scope_error").status_code == 403


def test_tenant_not_found_error_maps_to_404() -> None:
    app = create_app()

    @app.get("/_missing_tenant")
    def _raise_missing() -> None:
        raise TenantNotFoundError("ghost")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/_missing_tenant").status_code == 404
