"""Tests for the secret store, RBAC-lite, and access audit (M45)."""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.auth.keys import ApiKeyService, hash_api_key
from hiveplane.auth.models import (
    AccessResult,
    AuthenticationError,
    AuthMethod,
    AuthorizationError,
    Permission,
    Scope,
)
from hiveplane.auth.rbac import effective_permissions, has_permission
from hiveplane.auth.service import AuthService
from hiveplane.auth.store import InMemoryAuthStore
from hiveplane.secrets.crypto import KeyProvider, LocalKeyProvider, load_or_create_master_key
from hiveplane.secrets.injection import SecretInjector
from hiveplane.secrets.models import (
    SecretError,
    SecretInjection,
    SecretLeakError,
    SecretNotFoundError,
    SecretRef,
    SecretResolutionError,
)
from hiveplane.secrets.redaction import RedactionLogFilter, Redactor, RedactorRegistry
from hiveplane.secrets.service import SecretAlreadyExistsError, SecretService
from hiveplane.secrets.store import InMemorySecretStore
from hiveplane.tenancy import TenantContext
from hiveplane.tenancy.models import Role

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_MASTER = b"0" * 32


_TA = TenantContext(tenant_id="tenant-a", role=Role.ADMIN)
_T = TenantContext(tenant_id="t", role=Role.ADMIN)
_DEFAULT = TenantContext(tenant_id="default", role=Role.ADMIN)
_TB = TenantContext(tenant_id="tenant-b", role=Role.ADMIN)


def _service(*, provider: KeyProvider | None = None) -> SecretService:
    ids = iter(f"sec-{n}" for n in range(1, 100))
    return SecretService(
        InMemorySecretStore(),
        provider or LocalKeyProvider(_MASTER),
        clock=lambda: _NOW,
        id_factory=lambda: next(ids),
    )


# --------------------------------------------------------------------------- #
# M45-01 — encrypted store
# --------------------------------------------------------------------------- #
def test_secret_ref_parsing_and_rendering() -> None:
    assert SecretRef.parse("secret://tenant-a/pg@3").render() == "secret://tenant-a/pg@3"
    latest = SecretRef.parse("secret://tenant-a/pg")
    assert latest.version is None
    assert latest.render() == "secret://tenant-a/pg"
    for bad in ("pg-readonly", "secret://tenant", "secret:///pg", "secret://t/pg@x"):
        with pytest.raises(SecretError):
            SecretRef.parse(bad)


def test_put_stores_ciphertext_not_plaintext() -> None:
    service = _service()
    service.put("tenant-a", "pg-readonly", "s3cr3t-value", ctx=_TA)

    metadata = service.metadata("tenant-a", "pg-readonly", ctx=_TA)
    assert metadata.current_version == 1
    assert metadata.versions == [1]

    resolved = service.resolve(SecretRef(tenant_id="tenant-a", name="pg-readonly"))
    assert resolved.value == "s3cr3t-value"
    assert resolved.ref.version == 1

    stored = service._store.get_version("sec-1", 1, ctx=_TA)
    assert b"s3cr3t-value" not in stored.ciphertext  # type: ignore[union-attr]
    assert b"s3cr3t-value" not in stored.wrapped_key  # type: ignore[union-attr]


def test_put_twice_is_rejected() -> None:
    service = _service()
    service.put("t", "n", "v", ctx=_T)
    with pytest.raises(SecretAlreadyExistsError):
        service.put("t", "n", "v2", ctx=_T)


def test_resolve_fails_closed_on_unknown() -> None:
    service = _service()
    with pytest.raises(SecretResolutionError):
        service.resolve(SecretRef(tenant_id="t", name="missing"))
    service.put("t", "n", "v", ctx=_T)
    with pytest.raises(SecretResolutionError):
        service.resolve(SecretRef(tenant_id="t", name="n", version=99))


# --------------------------------------------------------------------------- #
# M45-04 — rotation and version pinning
# --------------------------------------------------------------------------- #
def test_rotation_takes_effect_for_latest_but_not_pinned() -> None:
    service = _service()
    service.put("t", "token", "old", ctx=_T)
    service.rotate("t", "token", "new", ctx=_T)

    assert service.metadata("t", "token", ctx=_T).current_version == 2
    assert service.resolve(SecretRef(tenant_id="t", name="token")).value == "new"
    assert service.resolve(SecretRef(tenant_id="t", name="token", version=1)).value == "old"
    assert service.resolve(SecretRef(tenant_id="t", name="token", version=2)).value == "new"


def test_revoked_version_cannot_be_resolved() -> None:
    service = _service()
    service.put("t", "token", "old", ctx=_T)
    service.rotate("t", "token", "new", ctx=_T)
    service.revoke_version("t", "token", 1, ctx=_T)

    with pytest.raises(SecretResolutionError):
        service.resolve(SecretRef(tenant_id="t", name="token", version=1))
    with pytest.raises(SecretNotFoundError):
        service.revoke_version("t", "token", 99, ctx=_T)


def test_consumers_are_tracked() -> None:
    service = _service()
    service.put("t", "token", "v", ctx=_T)
    service.resolve(SecretRef(tenant_id="t", name="token"), workload="repo-agent")
    assert service.consumers("t", "token") == ["repo-agent"]
    assert service.metadata("t", "token", ctx=_T).consumers == ["repo-agent"]


def test_crypto_round_trip_and_tamper() -> None:
    provider = LocalKeyProvider(b"1" * 32, key_id="k1")
    encrypted = provider.encrypt(b"hello")
    assert encrypted.key_id == "k1"
    assert (
        provider.decrypt(
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            wrapped_key=encrypted.wrapped_key,
            key_nonce=encrypted.key_nonce,
            key_id=encrypted.key_id,
        )
        == b"hello"
    )
    other = LocalKeyProvider(b"2" * 32)
    with pytest.raises(SecretError):
        other.decrypt(
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            wrapped_key=encrypted.wrapped_key,
            key_nonce=encrypted.key_nonce,
            key_id=encrypted.key_id,
        )
    with pytest.raises(SecretError):
        LocalKeyProvider(b"short")


def test_load_or_create_master_key(tmp_path: object) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "secrets" / "master.key"
    key = load_or_create_master_key(path)
    assert len(key) == 32
    assert load_or_create_master_key(path) == key

    path.write_text("not-hex")
    with pytest.raises(SecretError):
        load_or_create_master_key(path)


# --------------------------------------------------------------------------- #
# M45-02/03 — injection and redaction / absence
# --------------------------------------------------------------------------- #
def test_inject_env_and_file_register_with_redactor() -> None:
    service = _service()
    service.put("t", "api-token", "canary-xyz", ctx=_T)
    redactors = RedactorRegistry()
    injector = SecretInjector(service, redactors)
    ref = SecretRef(tenant_id="t", name="api-token")

    env = injector.inject(
        ref, SecretInjection.model_validate({"as": "env", "name": "PROVIDER_TOKEN"}), run_id="r1"
    )
    assert env.env == {"PROVIDER_TOKEN": "canary-xyz"}
    assert redactors.for_run("r1").redact("value=canary-xyz") == "value=[REDACTED]"

    file = injector.inject(
        ref, SecretInjection.model_validate({"as": "file", "path": "/run/secrets/x"}), run_id="r2"
    )
    assert file.files == {"/run/secrets/x": "canary-xyz"}
    assert file.mode == "0400"

    injector.release("r1")
    assert len(redactors.for_run("r1")) == 0


def test_secret_absent_from_every_sink() -> None:
    canary = "sk-live-CANARY-1234567890"
    redactor = Redactor()
    redactor.register(canary)

    # agent context, tool args, audit event, fan-out payload, artifact
    sinks = {
        "context": f"system prompt with token {canary}",
        "trace": f"span.attributes={{'token': '{canary}'}}",
        "audit": f"actor=alice action=resolve detail={canary}",
        "fanout": f'{{"slack": "secret {canary}"}}',
        "artifact": f"log line: {canary}",
    }
    for sink, text in sinks.items():
        with pytest.raises(SecretLeakError):
            redactor.assert_absent(text, sink)
        assert canary not in redactor.redact(text)

    # logging filter redacts in place
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionLogFilter(redactor))
    logger = logging.getLogger("m45-absence")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.info("leaking %s", canary)
    assert canary not in stream.getvalue()
    assert "[REDACTED]" in stream.getvalue()

    redactor.clear()
    assert not redactor.contains(canary)


# --------------------------------------------------------------------------- #
# M45-05 — RBAC-lite and scoped keys
# --------------------------------------------------------------------------- #
def test_role_permissions() -> None:
    assert Permission.APPROVE in effective_permissions(Role.APPROVER)
    assert Permission.KILL_SWITCH not in effective_permissions(Role.APPROVER)
    assert effective_permissions(Role.VIEWER) == {Permission.FLEET_READ}
    assert effective_permissions(Role.ADMIN, [Scope.RUNS_READ]) == {Permission.FLEET_READ}
    assert Permission.ARTIFACTS_MANAGE in effective_permissions(Role.ADMIN)
    assert Permission.ARTIFACTS_MANAGE not in effective_permissions(Role.VIEWER)
    assert effective_permissions(
        Role.ADMIN, [Scope.ARTIFACTS_WRITE]
    ) == {Permission.ARTIFACTS_MANAGE}


def test_api_key_lifecycle() -> None:
    store = InMemoryAuthStore()
    service = ApiKeyService(
        store,
        clock=lambda: _NOW,
        key_id_factory=lambda: "key-1",
        token_factory=lambda: "hp_token",
    )
    issued = service.create("tenant-a", Role.APPROVER, scopes=[Scope.APPROVALS_WRITE], ctx=_TA)
    assert issued.token == "hp_token"
    assert issued.record.hashed_key == hash_api_key("hp_token")
    assert "hp_token" not in issued.record.hashed_key

    identity = service.authenticate("hp_token")
    assert identity.role is Role.APPROVER
    assert identity.method is AuthMethod.API_KEY
    assert store.get_key("key-1", ctx=_TA).last_used_at == _NOW  # type: ignore[union-attr]

    with pytest.raises(AuthenticationError):
        service.authenticate("wrong")

    service.revoke("key-1", ctx=_TA)
    with pytest.raises(AuthenticationError):
        service.authenticate("hp_token")
    with pytest.raises(AuthenticationError):
        service.revoke("missing", ctx=_TA)


def test_scoped_key_cannot_exceed_scope() -> None:
    store = InMemoryAuthStore()
    auth = AuthService(store, clock=lambda: _NOW)
    issued = auth.keys.create(
        "tenant-a", Role.ADMIN, scopes=[Scope.RUNS_READ], label="read-only admin", ctx=_TA
    )
    identity = auth.authenticate_key(issued.token)

    assert has_permission(identity, Permission.FLEET_READ)
    assert not has_permission(identity, Permission.APPROVE)
    assert not has_permission(identity, Permission.KILL_SWITCH)


def test_viewer_cannot_approve_promote_or_kill_and_is_audited() -> None:
    store = InMemoryAuthStore()
    auth = AuthService(store, clock=lambda: _NOW)
    viewer = auth.login("bob", "tenant-a", Role.VIEWER)

    for permission in (Permission.APPROVE, Permission.PROMOTE, Permission.KILL_SWITCH):
        with pytest.raises(AuthorizationError):
            auth.authorize(viewer, permission, action=permission.value, target="run-1")

    denials = [e for e in auth.access_history("tenant-a", ctx=_TA) if e.result is AccessResult.DENY]
    assert {e.action for e in denials} == {"approve", "promote", "kill_switch"}


def test_admin_action_is_audited_and_login_recorded() -> None:
    store = InMemoryAuthStore()
    auth = AuthService(store, clock=lambda: _NOW)
    admin = auth.login("alice", "tenant-a", Role.ADMIN, ip="10.0.0.1")

    auth.authorize(admin, Permission.APPROVE, action="approve", target="run-1")

    logins = auth.login_history("tenant-a", ctx=_TA)
    assert logins[0].actor == "alice" and logins[0].ip == "10.0.0.1"
    events = auth.access_history("tenant-a", ctx=_TA)
    assert events[-1].result is AccessResult.ALLOW
    assert auth.whoami(admin).operator_id == "alice"


def test_injected_secret_missing_fails_closed() -> None:
    service = _service()
    injector = SecretInjector(service, RedactorRegistry())
    with pytest.raises(SecretResolutionError):
        injector.inject(
            SecretRef(tenant_id="t", name="missing"), None, run_id="r1", workload="w"
        )


# --------------------------------------------------------------------------- #
# M45-08 — API
# --------------------------------------------------------------------------- #
def _client() -> Any:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    return TestClient(create_app())


def test_secrets_api_lifecycle_never_returns_value() -> None:
    client = _client()
    created = client.post("/secrets", json={"name": "pg", "value": "canary-value"})
    assert created.status_code == 201
    assert "value" not in created.json()

    rotated = client.post("/secrets/pg/rotate", json={"name": "pg", "value": "new-value"})
    assert rotated.json()["current_version"] == 2
    assert client.get("/secrets").json()[0]["name"] == "pg"
    assert client.get("/secrets/pg").json()["versions"] == [1, 2]

    assert client.post("/secrets", json={"name": "pg", "value": "x"}).status_code == 409
    assert client.get("/secrets/missing").status_code == 404
    assert (
        client.post("/secrets/missing/rotate", json={"name": "missing", "value": "x"}).status_code
        == 404
    )


def test_keys_auth_and_audit_api() -> None:
    client = _client()
    issued = client.post("/keys", json={"role": "approver", "scopes": ["approvals:write"]})
    assert issued.status_code == 201
    token = issued.json()["token"]
    key_id = issued.json()["key_id"]
    listed = client.get("/keys").json()[0]
    assert listed["hashed_key"] != token

    assert client.delete(f"/keys/{key_id}").json()["revoked_at"] is not None
    assert client.delete("/keys/missing").status_code == 404

    assert client.post(
        "/auth/login", json={"operator_id": "alice", "tenant_id": "default", "role": "admin"}
    ).status_code == 200
    assert client.get("/auth/whoami").json()["operator_id"] == "anonymous"
    assert client.get("/audit/access").json()["logins"]


def test_audit_access_endpoint_is_bounded() -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    app = create_app()
    client = TestClient(app)
    auth = app.state.auth_service
    for i in range(5):
        auth.login(f"alice-{i}", "default", Role.VIEWER)
        auth.authorize(
            auth.login(f"bob-{i}", "default", Role.ADMIN),
            Permission.FLEET_READ,
            action="fleet_read",
        )

    bounded = client.get("/audit/access", params={"limit": 2}).json()
    assert len(bounded["logins"]) == 2
    assert len(bounded["events"]) == 2
    assert client.get("/audit/access", params={"limit": 999999}).status_code == 422


def test_session_login_rejected_when_auth_enabled(monkeypatch: object) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")  # type: ignore[attr-defined]
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.tenancy.context import SYSTEM_CONTEXT

    app = create_app()
    client = TestClient(app)

    refused = client.post(
        "/auth/login", json={"operator_id": "cfo", "tenant_id": "acme", "role": "admin"}
    )

    assert refused.status_code == 403
    assert app.state.auth_service.store.list_logins("acme", ctx=SYSTEM_CONTEXT) == []


def test_scoped_viewer_key_is_denied_server_side(monkeypatch: object) -> None:

    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")  # type: ignore[attr-defined]
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.tenancy.models import Role

    app = create_app()
    admin = app.state.auth_service.keys.create("default", Role.ADMIN)
    viewer = app.state.auth_service.keys.create("default", Role.VIEWER)
    client = TestClient(app)

    # no token -> 401
    assert client.get("/secrets").status_code == 401
    # admin can manage secrets
    assert (
        client.post(
            "/secrets",
            json={"name": "s", "value": "v"},
            headers={"Authorization": f"Bearer {admin.token}"},
        ).status_code
        == 201
    )
    # viewer cannot manage secrets -> 403
    denied = client.get("/secrets", headers={"Authorization": f"Bearer {viewer.token}"})
    assert denied.status_code == 403
    # viewer cannot approve -> 403 (checked before the approval is looked up)
    assert (
        client.post(
            "/approvals/missing/approve",
            json={"operator": "bob"},
            headers={"Authorization": f"Bearer {viewer.token}"},
        ).status_code
        == 403
    )


def test_cross_tenant_key_operations_are_isolated(monkeypatch: object) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")  # type: ignore[attr-defined]
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.tenancy.context import SYSTEM_CONTEXT

    app = create_app()
    keys = app.state.auth_service.keys
    tenant_a = keys.create("tenant-a", Role.ADMIN, ctx=SYSTEM_CONTEXT)
    tenant_b = keys.create("tenant-b", Role.ADMIN, ctx=SYSTEM_CONTEXT)
    client = TestClient(app)
    headers_b = {"Authorization": f"Bearer {tenant_b.token}"}

    listed = client.get("/keys", headers=headers_b).json()
    assert [record["key_id"] for record in listed] == [tenant_b.record.key_id]
    assert client.delete(f"/keys/{tenant_a.record.key_id}", headers=headers_b).status_code == 404
    assert (
        client.delete(f"/keys/{tenant_b.record.key_id}", headers=headers_b).status_code
        == 200
    )


def test_suspended_tenant_key_is_refused_by_api(monkeypatch: object) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")  # type: ignore[attr-defined]
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.tenancy.context import SYSTEM_CONTEXT

    app = create_app()
    admin = app.state.tenant_admin_service
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="tenant-a", name="A")
    key = app.state.auth_service.keys.create("tenant-a", Role.ADMIN, ctx=SYSTEM_CONTEXT)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key.token}"}
    assert client.get("/keys", headers=headers).status_code == 200

    admin.suspend_tenant(SYSTEM_CONTEXT, "tenant-a")

    assert client.get("/keys", headers=headers).status_code == 403
    assert app.state.auth_service.keys.list_keys("tenant-a", ctx=SYSTEM_CONTEXT) != []


def test_suspended_tenant_login_is_refused(monkeypatch: object) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")  # type: ignore[attr-defined]
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.tenancy.context import SYSTEM_CONTEXT

    app = create_app()
    admin = app.state.tenant_admin_service
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    admin.suspend_tenant(SYSTEM_CONTEXT, "acme")
    client = TestClient(app)

    refused = client.post(
        "/auth/login", json={"operator_id": "alice", "tenant_id": "acme", "role": "admin"}
    )

    assert refused.status_code == 403


def test_postgres_secret_and_auth_stores(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.auth.models import AccessResult, ApiKeyRecord, AuthMethod, LoginEvent
    from hiveplane.auth.store import PostgresAuthStore
    from hiveplane.secrets.models import SecretRecord, SecretVersion
    from hiveplane.secrets.store import PostgresSecretStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)

    secret_store = PostgresSecretStore(pg_engine)
    secret_store.clear()
    record = SecretRecord(
        secret_id="sec-1",
        tenant_id="t",
        name="n",
        current_version=1,
        created_at=_NOW,
        versions=[1],
    )
    version = SecretVersion(
        secret_id="sec-1",
        tenant_id="t",
        name="n",
        version=1,
        ciphertext=b"c",
        nonce=b"n",
        wrapped_key=b"w",
        key_nonce=b"k",
        key_id="local",
        created_at=_NOW,
    )
    secret_store.save_secret(record, ctx=_T)
    secret_store.save_version(version, ctx=_T)
    secret_store.save_version(version, ctx=_T)
    reopened = PostgresSecretStore(pg_engine)
    assert reopened.get_secret("t", "n", ctx=_T) is not None
    assert reopened.get_secret("t", "missing", ctx=_T) is None
    assert reopened.list_secrets("t", ctx=_T)[0].name == "n"
    assert reopened.get_version("sec-1", 1, ctx=_T) is not None
    assert reopened.list_versions("sec-1", ctx=_T)[0].version == 1

    auth_store = PostgresAuthStore(pg_engine)
    auth_store.clear()
    key = ApiKeyRecord(
        key_id="key-1",
        tenant_id="t",
        role=Role.ADMIN,
        hashed_key="h",
        created_at=_NOW,
    )
    auth_store.save_key(key, ctx=_T)
    auth_store.save_key(key, ctx=_T)
    auth_store.save_login(
        LoginEvent(
            tenant_id="t",
            actor="alice",
            method=AuthMethod.SESSION,
            result=AccessResult.ALLOW,
            created_at=_NOW,
        ),
        ctx=_T,
    )
    auth_reopened = PostgresAuthStore(pg_engine)
    assert auth_reopened.get_key("key-1", ctx=_T) is not None
    assert auth_reopened.find_key_by_hash("h") is not None
    assert auth_reopened.list_keys("t", ctx=_T)[0].key_id == "key-1"
    assert auth_reopened.list_logins("t", ctx=_T)[0].actor == "alice"

    auth_store.clear()
    secret_store.clear()


# --------------------------------------------------------------------------- #
# Edge cases: stores, redactor, key usage
# --------------------------------------------------------------------------- #
def test_in_memory_stores_edges() -> None:
    from hiveplane.auth.store import InMemoryAuthStore
    from hiveplane.secrets.models import SecretRecord, SecretVersion
    from hiveplane.secrets.store import InMemorySecretStore

    secret_store = InMemorySecretStore()
    record = SecretRecord(
        secret_id="sec-1",
        tenant_id="t",
        name="a",
        current_version=1,
        created_at=_NOW,
        versions=[1],
    )
    version = SecretVersion(
        secret_id="sec-1",
        tenant_id="t",
        name="a",
        version=1,
        ciphertext=b"c",
        nonce=b"n",
        wrapped_key=b"w",
        key_nonce=b"k",
        key_id="local",
        created_at=_NOW,
    )
    secret_store.save_secret(record, ctx=_T)
    secret_store.save_version(version, ctx=_T)
    assert secret_store.list_secrets("t", ctx=_T)[0].name == "a"
    assert secret_store.list_secrets("other") == []
    assert secret_store.list_versions("sec-1", ctx=_T)[0].version == 1
    assert secret_store.get_version("sec-1", 99, ctx=_T) is None
    secret_store.clear()
    assert secret_store.list_secrets("t", ctx=_T) == []

    store = InMemoryAuthStore()
    keys = ApiKeyService(
        store, clock=lambda: _NOW, key_id_factory=lambda: "k", token_factory=lambda: "hp"
    )
    keys.create("t", Role.VIEWER, ctx=_T)
    assert store.get_key("k", ctx=_T) is not None
    assert store.get_key("missing", ctx=_T) is None
    assert store.find_key_by_hash("nope") is None
    assert store.list_keys("t", ctx=_T)[0].key_id == "k"
    assert keys.usage("t", ctx=_T)["k"] is None
    auth = AuthService(store, clock=lambda: _NOW)
    auth.login("a", "t", Role.ADMIN)
    assert store.list_logins("t", ctx=_T)[0].actor == "a"
    assert store.list_access("t", ctx=_T) == []
    store.clear()
    assert store.list_keys("t", ctx=_T) == []


def test_redactor_edges_and_key_provider_property() -> None:
    redactor = Redactor()
    redactor.register("")
    assert len(redactor) == 0
    redactor.register("v1")
    redactor.register("v1")
    assert len(redactor) == 1
    redactor.unregister("v1")
    assert len(redactor) == 0
    redactor.unregister("absent")

    registry = RedactorRegistry()
    registry.for_run("r").register("x")
    registry.clear()
    assert len(registry.for_run("r")) == 0

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionLogFilter(redactor))
    redactor.register("canary")
    logger = logging.getLogger("m45-filter-args")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.info("user=%s token=%s", "bob", "canary")
    assert "canary" not in stream.getvalue()

    provider = LocalKeyProvider(_MASTER, key_id="kid")
    assert provider.key_id == "kid"


def test_crypto_tampered_ciphertext_fails_closed() -> None:
    provider = LocalKeyProvider(_MASTER)
    encrypted = provider.encrypt(b"payload")
    tampered = bytes([encrypted.ciphertext[0] ^ 1]) + encrypted.ciphertext[1:]
    with pytest.raises(SecretError):
        provider.decrypt(
            ciphertext=tampered,
            nonce=encrypted.nonce,
            wrapped_key=encrypted.wrapped_key,
            key_nonce=encrypted.key_nonce,
            key_id=encrypted.key_id,
        )


def _auth_client_and_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, dict[str, str]]:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    app = create_app()
    viewer = app.state.auth_service.keys.create("default", Role.VIEWER)
    return TestClient(app), {"Authorization": f"Bearer {viewer.token}"}


def test_quarantine_requires_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers = _auth_client_and_viewer(monkeypatch)

    response = client.post(
        "/quarantines", json={"workload": "w", "reason": "r"}, headers=headers
    )

    assert response.status_code == 403


def test_reinstate_requires_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers = _auth_client_and_viewer(monkeypatch)

    response = client.post(
        "/quarantines/q1/reinstate", json={"operator": "bob"}, headers=headers
    )

    assert response.status_code == 403


def test_certification_requires_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers = _auth_client_and_viewer(monkeypatch)

    response = client.post(
        "/certifications", json={"workload": "w"}, headers=headers
    )

    assert response.status_code == 403


def test_shadow_requires_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers = _auth_client_and_viewer(monkeypatch)

    response = client.post(
        "/shadow",
        json={"candidate_workload_id": "c", "production_run_id": "r"},
        headers=headers,
    )

    assert response.status_code == 403
# --------------------------------------------------------------------------- #
# #540 — runtime injection, cross-tenant rejection, and sink redaction
# --------------------------------------------------------------------------- #
def test_run_boundary_injects_secret_env_for_workload(
    make_manifest: Any,
) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.tenancy.context import context_for_run

    app = create_app()
    TestClient(app)
    app.state.secret_service.put("default", "pg", "canary-value", ctx=_DEFAULT)
    manifest = make_manifest(
        name="agent-1",
        secrets=[
            {
                "ref": "secret://default/pg@1",
                "injection": {"as": "env", "name": "PG_DSN"},
            }
        ],
    )

    env = app.state.secret_injector.inject_for_workload(
        manifest, run_id="run-1", ctx=context_for_run("default")
    )

    assert env == {"PG_DSN": "canary-value"}


def test_cross_tenant_secret_ref_rejected(make_manifest: Any) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.tenancy.context import context_for_run

    app = create_app()
    TestClient(app)
    app.state.secret_service.put("tenant-b", "pg", "b-secret", ctx=_TB)
    manifest = make_manifest(
        name="agent-1",
        secrets=[{"ref": "secret://tenant-b/pg@1", "injection": {"as": "env"}}],
    )

    with pytest.raises(SecretResolutionError):
        app.state.secret_injector.inject_for_workload(
            manifest, run_id="run-1", ctx=context_for_run("tenant-a")
        )


def test_app_log_records_redact_resolved_secret(make_manifest: Any) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.tenancy.context import context_for_run

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        app = create_app()
        TestClient(app)
        app.state.secret_service.put("default", "pg", "canary-value", ctx=_DEFAULT)
        manifest = make_manifest(
            name="agent-1",
            secrets=[{"ref": "secret://default/pg@1", "injection": {"as": "env"}}],
        )
        app.state.secret_injector.inject_for_workload(
            manifest, run_id="run-1", ctx=context_for_run("default")
        )

        logging.getLogger("hiveplane.test").warning("resolved canary-value for run")
    finally:
        root.removeHandler(handler)

    assert "canary-value" not in stream.getvalue()
    assert "[REDACTED]" in stream.getvalue()


def test_secret_version_json_payload_round_trips_binary_ciphertext() -> None:
    """Postgres serializes SecretVersion via ``model_dump(mode="json")``.

    The ciphertext/nonce/key fields are raw ``bytes`` that are not valid UTF-8,
    so JSON serialization must base64-encode them or persistence raises
    ``UnicodeDecodeError`` (found by the v0.2.0 field test S16).
    """
    from hiveplane.secrets.models import SecretVersion

    version = SecretVersion(
        secret_id="sec-1",
        tenant_id="default",
        name="db-password",
        version=1,
        ciphertext=b"\x9f\x00\xff\x01",
        nonce=b"\x01\x02\x03",
        wrapped_key=b"\xde\xad\xbe\xef",
        key_nonce=b"\x04\x05\x06",
        key_id="hp-secret-key-01",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    payload = version.model_dump(mode="json")

    assert isinstance(payload["ciphertext"], str)
    assert SecretVersion.model_validate(payload) == version
