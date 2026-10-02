"""A fresh database must boot the full app (M61 field-test finding).

``create_app`` used to register the attestation signing key at import time, querying
``signing_keys`` before the lifespan ran migrations — a fresh deployment (empty volume,
first Helm install) crashed on boot. This test reproduces that path end-to-end against
a throwaway database.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from hiveplane.api.app import create_app
from hiveplane.config import get_settings

_FRESH_DB = "hiveplane_fresh_boot_test"


@pytest.fixture
def fresh_db(pg_engine: Engine) -> Iterator[str]:
    """Create a dedicated empty database for the boot; drop it afterwards."""
    url = get_settings().database.url
    admin = create_engine(url, pool_pre_ping=True, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {_FRESH_DB}"))
        connection.execute(text(f"CREATE DATABASE {_FRESH_DB}"))
    admin.dispose()
    yield _FRESH_DB
    cleanup = create_engine(url, pool_pre_ping=True, isolation_level="AUTOCOMMIT")
    with cleanup.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": _FRESH_DB},
        )
        connection.execute(text(f"DROP DATABASE IF EXISTS {_FRESH_DB}"))
    cleanup.dispose()


def test_fresh_database_boots_and_registers_the_signing_key(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    database = settings.database
    monkeypatch.setenv("HIVEPLANE_DATABASE__HOST", database.host)
    monkeypatch.setenv("HIVEPLANE_DATABASE__PORT", str(database.port))
    monkeypatch.setenv("HIVEPLANE_DATABASE__NAME", fresh_db)
    monkeypatch.setenv("HIVEPLANE_DATABASE__USER", database.user)
    monkeypatch.setenv(
        "HIVEPLANE_DATABASE__PASSWORD", database.password.get_secret_value()
    )
    monkeypatch.setenv("HIVEPLANE_EXECUTION__STORE", "postgres")
    get_settings.cache_clear()

    app = create_app()

    with TestClient(app) as client:
        # The regression is that the app boots at all (migrations + signing key).
        # Readiness may be 503 when no execution adapter is configured; that is a
        # legitimate signal, not a boot failure.
        response = client.get("/readyz")
        assert response.status_code in (200, 503), response.text
        key_id = app.state.artifact_signing_key_id
        resolved = app.state.signing_key_registry.resolve(key_id)
        assert resolved is not None, "the signing key must register after migrations"
