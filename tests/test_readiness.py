"""Tests for the honest readiness probe (M23, #130).

``/readyz`` must report not-ready with a specific reason whenever the control
plane cannot actually do its job: no runtime adapter attached, stores
unreachable, migrations missing, or no signing key loaded.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import StaticPool

from hiveplane.api.app import create_app
from hiveplane.api.readiness import ReadinessProbe, build_readiness_probe
from hiveplane.certification.store import InMemoryCertificationStore


class _BrokenCertificationStore:
    """A certification store whose backend is down."""

    def list(self, **kwargs: object) -> list[object]:
        raise RuntimeError("postgres down")


def _sqlite_engine() -> Engine:
    return create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _probe_with(engine: Engine | None) -> ReadinessProbe:
    return build_readiness_probe(
        engine=engine,
        get_adapter=lambda: object(),
        get_certification_store=lambda: InMemoryCertificationStore(),
        get_public_key=lambda: object(),
    )


def test_readyz_ready_when_all_dependencies_are_live() -> None:
    app = create_app()
    app.state.adapter = object()

    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_reports_not_ready_when_adapter_is_disabled() -> None:
    response = TestClient(create_app()).get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert any("adapter" in reason for reason in body["reasons"])


def test_readyz_reports_not_ready_when_certification_store_unreachable() -> None:
    app = create_app()
    app.state.adapter = object()
    app.state.certification_store = _BrokenCertificationStore()

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert any(
        "certification store" in reason for reason in response.json()["reasons"]
    )


def test_readyz_reports_not_ready_when_signing_key_missing() -> None:
    app = create_app()
    app.state.adapter = object()
    app.state.attestation_public_key = None

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert any("signing key" in reason for reason in response.json()["reasons"])


def test_probe_fails_database_check_when_engine_is_unreachable() -> None:
    engine = create_engine(
        "sqlite+pysqlite:////nonexistent-hiveplane-dir-xyz/does-not-exist.db"
    )

    report = _probe_with(engine).check()

    assert not report.ready
    database = next(check for check in report.checks if check.name == "database")
    assert database.ok is False
    assert database.reason is not None


def test_probe_fails_migrations_check_when_alembic_version_is_absent() -> None:
    engine = _sqlite_engine()

    report = _probe_with(engine).check()

    assert not report.ready
    migrations = next(check for check in report.checks if check.name == "migrations")
    assert migrations.ok is False


def test_probe_fails_migrations_check_when_alembic_version_is_empty() -> None:
    engine = _sqlite_engine()
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )

    report = _probe_with(engine).check()

    assert not report.ready
    migrations = next(check for check in report.checks if check.name == "migrations")
    assert migrations.ok is False


def test_readyz_reports_not_ready_when_certification_store_absent() -> None:
    app = create_app()
    app.state.adapter = object()
    app.state.certification_store = None

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert any(
        "certification store" in reason for reason in response.json()["reasons"]
    )


def test_probe_passes_when_alembic_version_is_populated() -> None:
    engine = _sqlite_engine()
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('abc123')")
        )

    report = _probe_with(engine).check()

    assert report.ready
    assert all(check.ok for check in report.checks)
