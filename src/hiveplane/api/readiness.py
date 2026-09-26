"""Honest readiness probe for the control plane (M23, #130).

A control plane is "ready" only when it can actually do its job: its stores are
reachable, migrations are applied, a runtime adapter is attached, and an
attestation signing key is loaded. Each dependency yields a named check with a
reason on failure so operators can see exactly what to fix.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from hiveplane.certification.store import CertificationStore
from hiveplane.tenancy import SYSTEM_CONTEXT

#: A probe that reads one dependency's current state.
Check = Callable[[], "ReadinessCheck"]


class ReadinessCheck(BaseModel):
    """One dependency's readiness, with the reason it is not ready."""

    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    reason: str | None = None


class ReadinessReport(BaseModel):
    """The aggregate readiness of every checked dependency."""

    model_config = ConfigDict(extra="forbid")

    ready: bool
    checks: list[ReadinessCheck]


class ReadinessProbe:
    """Runs every readiness dependency and aggregates the result."""

    def __init__(self, checks: list[Check]) -> None:
        self._checks = checks

    def check(self) -> ReadinessReport:
        """Evaluate every dependency and report ready only if all pass."""
        results = [check() for check in self._checks]
        return ReadinessReport(
            ready=all(result.ok for result in results), checks=results
        )


def build_readiness_probe(
    *,
    engine: Engine | None,
    get_adapter: Callable[[], object | None],
    get_certification_store: Callable[[], CertificationStore | None],
    get_public_key: Callable[[], object | None],
) -> ReadinessProbe:
    """Build the readiness probe over the control plane's live dependencies.

    ``engine`` is the system-of-record engine in Postgres mode and ``None`` when
    the run store is in-memory or JSON, in which case the database and migration
    checks are not applicable.
    """
    return ReadinessProbe(
        [
            lambda: _check_database(engine),
            lambda: _check_migrations(engine),
            lambda: _check_adapter(get_adapter()),
            lambda: _check_certification_store(get_certification_store()),
            lambda: _check_signing_key(get_public_key()),
        ]
    )


def _check_database(engine: Engine | None) -> ReadinessCheck:
    if engine is None:
        return ReadinessCheck(
            name="database", ok=True, reason="not applicable: run store is not postgres"
        )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        return ReadinessCheck(
            name="database", ok=False, reason=f"database unreachable: {error}"
        )
    return ReadinessCheck(name="database", ok=True)


def _check_migrations(engine: Engine | None) -> ReadinessCheck:
    if engine is None:
        return ReadinessCheck(
            name="migrations",
            ok=True,
            reason="not applicable: run store is not postgres",
        )
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).fetchone()
    except SQLAlchemyError as error:
        return ReadinessCheck(
            name="migrations",
            ok=False,
            reason=f"database migrations are not applied: {error}",
        )
    if row is None:
        return ReadinessCheck(
            name="migrations", ok=False, reason="database has no applied migrations"
        )
    return ReadinessCheck(name="migrations", ok=True)


def _check_adapter(adapter: object | None) -> ReadinessCheck:
    if adapter is None:
        return ReadinessCheck(
            name="adapter",
            ok=False,
            reason=(
                "execution adapter is not attached: set "
                "HIVEPLANE_EXECUTION__ADAPTER=raw-worker, langgraph, or auto so "
                "runs execute"
            ),
        )
    return ReadinessCheck(name="adapter", ok=True)


def _check_certification_store(
    store: CertificationStore | None,
) -> ReadinessCheck:
    if store is None:
        return ReadinessCheck(
            name="certification_store",
            ok=False,
            reason="certification store is not configured",
        )
    try:
        store.list(limit=1, ctx=SYSTEM_CONTEXT)
    except Exception as error:
        return ReadinessCheck(
            name="certification_store",
            ok=False,
            reason=f"certification store unreachable: {error}",
        )
    return ReadinessCheck(name="certification_store", ok=True)


def _check_signing_key(public_key: object | None) -> ReadinessCheck:
    if public_key is None:
        return ReadinessCheck(
            name="signing_key",
            ok=False,
            reason="attestation signing key is not loaded",
        )
    return ReadinessCheck(name="signing_key", ok=True)
