"""Tests for per-tenant API rate limiting (M56-04)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.api.ratelimit import TenantRateLimiter
from hiveplane.config import get_settings

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def test_limiter_allows_then_denies_with_retry_after() -> None:
    limiter = TenantRateLimiter(
        requests_per_window=2, window_seconds=60, clock=lambda: _NOW
    )

    assert limiter.check("t").allowed is True
    assert limiter.check("t").allowed is True
    denied = limiter.check("t")

    assert denied.allowed is False
    assert denied.remaining == 0
    assert denied.retry_after_seconds >= 1


def test_limiter_refills_over_time() -> None:
    moment = {"now": _NOW}
    limiter = TenantRateLimiter(
        requests_per_window=60, window_seconds=60, clock=lambda: moment["now"]
    )
    for _ in range(60):
        limiter.check("t")
    assert limiter.check("t").allowed is False

    moment["now"] = _NOW + timedelta(seconds=1)

    assert limiter.check("t").allowed is True


def test_tenants_have_independent_buckets() -> None:
    limiter = TenantRateLimiter(
        requests_per_window=1, window_seconds=60, clock=lambda: _NOW
    )
    assert limiter.check("a").allowed is True
    assert limiter.check("a").allowed is False
    assert limiter.check("b").allowed is True


def test_api_returns_429_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_API__RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("HIVEPLANE_API__RATE_LIMIT_REQUESTS", "2")
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/workloads").status_code == 200
    assert client.get("/workloads").status_code == 200
    limited = client.get("/workloads")

    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


def test_health_endpoints_are_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_API__RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("HIVEPLANE_API__RATE_LIMIT_REQUESTS", "1")
    get_settings.cache_clear()
    client = TestClient(create_app())

    for _ in range(5):
        assert client.get("/healthz").status_code == 200


def test_ratelimit_uses_authenticated_tenant_not_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    monkeypatch.setenv("HIVEPLANE_API__RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("HIVEPLANE_API__RATE_LIMIT_REQUESTS", "2")
    get_settings.cache_clear()
    from hiveplane.tenancy.models import Role

    app = create_app()
    key = app.state.auth_service.keys.create("default", Role.ADMIN)
    client = TestClient(app)

    statuses = [
        client.get(
            "/workloads",
            headers={
                "Authorization": f"Bearer {key.token}",
                "X-Hiveplane-Tenant": f"spoof-{index}",
            },
        ).status_code
        for index in range(3)
    ]

    assert statuses[0] != 429
    assert statuses[1] != 429
    assert statuses[2] == 429


def test_spoofed_header_cannot_drain_other_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_API__RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("HIVEPLANE_API__RATE_LIMIT_REQUESTS", "1")
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert (
        client.get("/workloads", headers={"X-Hiveplane-Tenant": "victim"}).status_code
        == 200
    )
    assert (
        client.get("/workloads", headers={"X-Hiveplane-Tenant": "other"}).status_code
        == 429
    )
