# State Store & Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make run state and audit history durable in PostgreSQL — a full schema with Alembic migrations, a Postgres-backed `RunStore`, and a tamper-evident audit log — so a paused run survives a kill-and-restart and a mutated audit record is detectable.

**Architecture:** A new `hiveplane.persistence` package holds SQLAlchemy 2.0 declarative models (schema source of truth), Alembic migrations, a `PostgresRunStore` that maps ORM rows to the existing pydantic domain models, and an audit log with a chained SHA-256 hash. The existing `RunStore` / `RunService` / pydantic contracts are unchanged; Postgres is selected by `HIVEPLANE_EXECUTION__STORE=postgres`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic 1.20, psycopg 3, PostgreSQL 16, pytest / ruff / mypy strict, GitHub Actions.

## Global Constraints

- Python `>=3.12`; add only `alembic>=1.13` to core dependencies (`sqlalchemy`, `psycopg[binary]` already present).
- Every task must leave `ruff check`, `mypy src/ tests/`, and `pytest --cov` green (>95% locally).
- Ruff line length 100; `ANN` enforced in `src/`, ignored in `tests/`.
- Mypy strict; SQLAlchemy 2.0 typed declarative (`Mapped[...]`, `mapped_column`).
- Timezone-aware datetimes everywhere; `DateTime(timezone=True)` columns.
- No code comments; docstrings in repo style.
- Reuse existing contracts: `RunStore`/`RunBundle` (`execution/store.py`), `Run`, `RunEvent`, `RunState`, `UsageReport`, `AdmissionResult`, `DeliveryRecord`, `get_settings().database.url`.
- **Local vs CI:** Postgres-gated tests **skip** when no database is reachable; CI runs a `postgres:16` service. Exclude live-DB glue and Alembic boilerplate from coverage so the local gate stays green; behavior is still asserted by the gated tests.
- The 15 design tables plus one implementation table (`run_admissions`) — admissions are needed by `RunStore` and are absent from the design's entity list; document the addition.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | `alembic` core dep; `ExecutionSettings.store` literal; coverage omit |
| `src/hiveplane/config.py` | `store: Literal["memory", "json", "postgres"]` |
| `src/hiveplane/persistence/__init__.py` | package marker |
| `src/hiveplane/persistence/base.py` | `Base`, engine/session factory |
| `src/hiveplane/persistence/models.py` | ORM models for all tables + indexes |
| `src/hiveplane/persistence/run_store.py` | `PostgresRunStore` |
| `src/hiveplane/persistence/audit.py` | `AuditChain` (pure), `AuditRecord`, `AuditLog` protocol, `InMemoryAuditLog` |
| `src/hiveplane/persistence/postgres_audit.py` | `PostgresAuditLog` |
| `alembic.ini` | Alembic config |
| `src/hiveplane/persistence/migrations/env.py` | Alembic env (URL from settings, metadata target) |
| `src/hiveplane/persistence/migrations/versions/0001_initial_schema.py` | initial revision |
| `src/hiveplane/execution/store.py` | (unchanged protocol) |
| `src/hiveplane/execution/wiring.py` | `build_run_store` selects Postgres |
| `src/hiveplane/execution/service.py` | optional `audit: AuditLog | None` |
| `.github/workflows/ci.yml` | `postgres:16` service + migrations |
| `tests/postgres.py` | shared availability fixture/helper |
| `tests/test_persistence_*.py` | schema, audit, migrations, run-store tests |
| `docs/design/state-store-design.md`, WBS files | docs/status |

---

### Task 1: Dependency, config, coverage

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/hiveplane/config.py`
- Test: `tests/test_persistence_config.py`

**Interfaces:**
- Produces: `ExecutionSettings.store` accepts `"postgres"`; `alembic` core dependency; coverage omits.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for persistence configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path

from hiveplane.config import Settings

_ROOT = Path(__file__).resolve().parents[1]


def test_postgres_store_is_selectable() -> None:
    settings = Settings(execution={"store": "postgres"})
    assert settings.execution.store == "postgres"


def test_alembic_is_a_core_dependency() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any("alembic" in dep for dep in data["project"]["dependencies"])


def test_coverage_omits_live_db_glue() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    omit = data["tool"]["coverage"]["run"]["omit"]
    assert "src/hiveplane/persistence/run_store.py" in omit
    assert "src/hiveplane/persistence/postgres_audit.py" in omit
    assert "src/hiveplane/persistence/migrations/*" in omit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence_config.py -q`
Expected: FAIL — `store` literal rejects `"postgres"`, `alembic` absent, `omit` missing

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/config.py`:

```python
class ExecutionSettings(BaseModel):
    """Run-store configuration (DD-05) and runtime execution selection."""

    store: Literal["memory", "json", "postgres"] = "json"
    data_dir: str = ".hiveplane/runs"
    entrypoints_root: str = "."
    adapter: Literal["none", "raw-worker"] = "none"
```

`pyproject.toml`:

```toml
dependencies = [
  # ... existing ...
  "alembic>=1.13",
]
```

```toml
[tool.coverage.run]
branch = true
source = ["src/hiveplane"]
omit = [
  "src/hiveplane/persistence/run_store.py",
  "src/hiveplane/persistence/postgres_audit.py",
  "src/hiveplane/persistence/migrations/*",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persistence_config.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/hiveplane/config.py tests/test_persistence_config.py
git commit -m "build(persistence): add alembic, postgres store option, coverage omit"
```

---

### Task 2: ORM base and schema

**Files:**
- Create: `src/hiveplane/persistence/__init__.py`
- Create: `src/hiveplane/persistence/base.py`
- Create: `src/hiveplane/persistence/models.py`
- Test: `tests/test_persistence_schema.py`

**Interfaces:**
- Consumes: `DatabaseSettings`.
- Produces:
  - `Base` (declarative base) with `metadata`.
  - `create_engine_from_settings(settings: Settings | None = None) -> Engine` and `SessionFactory` (`sessionmaker[Session]`).
  - ORM models: `WorkloadRow`, `WorkloadVersionRow`, `RunRow`, `RunEventRow`, `UsageEventRow`, `RunAdmissionRow`, `AuditRow`, `ApprovalRow`, `CertificationRow`, `AttestationRow`, `ToolRow`, `TriggerRuleRow`, `DriftScheduleRow`, `FanOutDeliveryRow`, `HealthSignalRow`, `CostAttributionRow`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the persistence schema (no database required)."""

from __future__ import annotations

from hiveplane.persistence.base import Base, create_engine_from_settings
from hiveplane.persistence import models  # noqa: F401  (registers tables)


def _index_names(table: str) -> set[str]:
    return {index.name for index in Base.metadata.tables[table].indexes}


def test_all_design_tables_exist() -> None:
    expected = {
        "workloads",
        "workload_versions",
        "runs",
        "run_events",
        "usage_events",
        "audit_log",
        "approvals",
        "certifications",
        "attestations",
        "tools",
        "trigger_rules",
        "drift_schedules",
        "fan_out_deliveries",
        "health_signals",
        "cost_attributions",
        "run_admissions",
    }
    assert expected <= set(Base.metadata.tables)


def test_run_query_indexes_exist() -> None:
    assert "ix_runs_workload_state_created" in _index_names("runs")
    assert "ix_run_events_run_timestamp" in _index_names("run_events")
    assert "ix_usage_events_run_timestamp" in _index_names("usage_events")
    assert "ix_fan_out_deliveries_run_status" in _index_names("fan_out_deliveries")
    assert "ix_audit_log_subject" in _index_names("audit_log")


def test_engine_is_created_from_settings() -> None:
    engine = create_engine_from_settings()
    assert engine.url.drivername == "postgresql+psycopg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence_schema.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.persistence'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/persistence/__init__.py`:

```python
"""PostgreSQL system of record: schema, migrations, stores (M18)."""
```

`src/hiveplane/persistence/base.py`:

```python
"""SQLAlchemy base, engine, and session factory (M18)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from hiveplane.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for all persistence models."""


def create_engine_from_settings(settings: Settings | None = None) -> Engine:
    """Create a SQLAlchemy engine for the configured database."""
    resolved = settings or get_settings()
    return create_engine(resolved.database.url, pool_pre_ping=True, future=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
```

`src/hiveplane/persistence/models.py` — all tables. Use JSONB for payloads and typed columns for query fields:

```python
"""ORM models for the HivePlane system of record (M18, D7).

Typed columns carry the fields queried for fleet/budget/audit views; a JSONB
``payload`` on each row stores the exact pydantic domain record so round-trips
are lossless. ``run_admissions`` is an implementation addition needed by
``RunStore`` (admissions are absent from the design's entity list).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from hiveplane.persistence.base import Base

_PAYLOAD = JSONB


class WorkloadRow(Base):
    """Current manifest and certification status for a workload."""

    __tablename__ = "workloads"

    name: Mapped[str] = mapped_column(String(253), primary_key=True)
    owner: Mapped[str] = mapped_column(String(253))
    team: Mapped[str | None] = mapped_column(String(253), nullable=True)
    certification_status: Mapped[str] = mapped_column(String(32), index=True)
    current_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class WorkloadVersionRow(Base):
    """Append-only manifest version history."""

    __tablename__ = "workload_versions"
    __table_args__ = (UniqueConstraint("workload", "version", name="uq_workload_versions"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workload: Mapped[str] = mapped_column(ForeignKey("workloads.name", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunRow(Base):
    """A single run and its current state."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_workload_state_created", "workload_id", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload_id: Mapped[str] = mapped_column(String(253), index=True)
    caller: Mapped[str] = mapped_column(String(253))
    state: Mapped[str] = mapped_column(String(32), index=True)
    context: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_identity: Mapped[str | None] = mapped_column(String(253), nullable=True)
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False)
    sandbox_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunEventRow(Base):
    """Append-only run transition log."""

    __tablename__ = "run_events"
    __table_args__ = (
        Index("ix_run_events_run_timestamp", "run_id", "timestamp"),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(253))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class UsageEventRow(Base):
    """Token/tool usage for budget and cost attribution."""

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_run_timestamp", "run_id", "timestamp"),
        Index("ix_usage_events_model_timestamp", "model_identity", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    model_identity: Mapped[str | None] = mapped_column(String(253), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunAdmissionRow(Base):
    """The admission decision recorded for a run."""

    __tablename__ = "run_admissions"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    outcome: Mapped[str] = mapped_column(String(32))
    context: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class AuditRow(Base):
    """Tamper-evident operator/policy audit records (chained hash)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_subject", "subject"),
        Index("ix_audit_log_created", "created_at"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(253))
    action: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class ApprovalRow(Base):
    """Pending and resolved approval requests."""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CertificationRow(Base):
    """Certification pipeline records."""

    __tablename__ = "certifications"

    certification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class AttestationRow(Base):
    """Immutable signed attestations."""

    __tablename__ = "attestations"
    __table_args__ = (Index("ix_attestations_workload_created", "workload", "created_at"),)

    attestation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253))
    model_identity: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class ToolRow(Base):
    """MCP tool registry entries."""

    __tablename__ = "tools"

    tool_id: Mapped[str] = mapped_column(String(253), primary_key=True)
    mcp_server: Mapped[str] = mapped_column(String(253), index=True)
    trust_level: Mapped[str] = mapped_column(String(32))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TriggerRuleRow(Base):
    """Trigger rules per workload."""

    __tablename__ = "trigger_rules"
    __table_args__ = (Index("ix_trigger_rules_workload_type", "workload", "type"),)

    trigger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253))
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class DriftScheduleRow(Base):
    """Re-certification schedules."""

    __tablename__ = "drift_schedules"
    __table_args__ = (Index("ix_drift_schedules_next_run", "next_re_cert_run"),)

    schedule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    next_re_cert_run: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class FanOutDeliveryRow(Base):
    """Result fan-out delivery attempts."""

    __tablename__ = "fan_out_deliveries"
    __table_args__ = (Index("ix_fan_out_deliveries_run_status", "run_id", "status"),)

    delivery_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64))
    destination_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class HealthSignalRow(Base):
    """Agent health signals."""

    __tablename__ = "health_signals"
    __table_args__ = (Index("ix_health_signals_workload_updated", "workload", "last_updated"),)

    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workload: Mapped[str] = mapped_column(String(253))
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CostAttributionRow(Base):
    """Cost showback records."""

    __tablename__ = "cost_attributions"
    __table_args__ = (Index("ix_cost_attributions_team_period", "team", "period", "workload"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team: Mapped[str | None] = mapped_column(String(253), nullable=True)
    workload: Mapped[str] = mapped_column(String(253))
    period: Mapped[str] = mapped_column(String(32))
    total_spend_usd: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)
```

Notes for the implementer:
- `Text` and `_PAYLOAD = JSONB` are used; drop any unused imports (`Text` is unused here — remove it) to satisfy ruff.
- SQLAlchemy reserves `metadata` on declarative classes; that is why workload metadata is carried in `payload`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persistence_schema.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/persistence/__init__.py src/hiveplane/persistence/base.py src/hiveplane/persistence/models.py tests/test_persistence_schema.py
git commit -m "feat(persistence): add ORM base and full schema"
```

---

### Task 3: Alembic migrations

**Files:**
- Create: `alembic.ini`
- Create: `src/hiveplane/persistence/migrations/env.py`
- Create: `src/hiveplane/persistence/migrations/script.py.mako`
- Create: `src/hiveplane/persistence/migrations/versions/0001_initial_schema.py`
- Create: `tests/postgres.py`
- Test: `tests/test_persistence_migrations.py`

**Interfaces:**
- Consumes: `Base.metadata`, `get_settings().database.url`.
- Produces: `alembic upgrade head` / `downgrade base`; shared `postgres_engine` fixture.

- [ ] **Step 1: Write the shared Postgres helper**

`tests/postgres.py`:

```python
"""Shared helpers for Postgres-gated tests (skip when no database is reachable)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from hiveplane.config import get_settings


def postgres_engine() -> Engine:
    """Return an engine for the configured database, or skip the test."""
    get_settings.cache_clear()
    url = get_settings().database.url
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"postgres not available at {url}: {exc}")
    return engine


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    """Provide a live Postgres engine (skips when unavailable)."""
    engine = postgres_engine()
    try:
        yield engine
    finally:
        engine.dispose()
```

- [ ] **Step 2: Write the failing test**

`tests/test_persistence_migrations.py`:

```python
"""Migration upgrade/downgrade and index-usage tests (Postgres-gated)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from postgres import pg_engine  # noqa: F401

_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "src/hiveplane/persistence/migrations"))
    return config


def test_upgrade_downgrade_upgrade(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    tables = set(inspect(pg_engine).get_table_names())
    assert {"runs", "run_events", "usage_events", "audit_log", "fan_out_deliveries"} <= tables

    command.downgrade(config, "base")
    remaining = set(inspect(pg_engine).get_table_names())
    assert "runs" not in remaining

    command.upgrade(config, "head")
    assert "runs" in set(inspect(pg_engine).get_table_names())


def test_run_fleet_query_uses_index(pg_engine: Engine) -> None:
    with pg_engine.begin() as connection:
        plan = connection.execute(
            text(
                "EXPLAIN SELECT id FROM runs "
                "WHERE workload_id = 'agent-1' AND state = 'running' "
                "ORDER BY created_at"
            )
        ).scalars().all()
    joined = "\n".join(plan)
    assert "ix_runs_workload_state_created" in joined
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence_migrations.py -q`
Expected: FAIL/SKIP — no `alembic.ini`; with Postgres running, FAIL on missing config.

- [ ] **Step 4: Write the Alembic scaffolding**

`alembic.ini`:

```ini
[alembic]
script_location = src/hiveplane/persistence/migrations
prepend_sys_path = .
version_path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`src/hiveplane/persistence/migrations/env.py`:

```python
"""Alembic environment: URL from settings, metadata from the ORM models."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from hiveplane.config import get_settings
from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database.url


def run_migrations_offline() -> None:
    """Emit SQL without a database connection."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    section = context.config.get_section(context.config.config_ini_section) or {}
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`src/hiveplane/persistence/migrations/script.py.mako` (standard Alembic template):

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`src/hiveplane/persistence/migrations/versions/0001_initial_schema.py`:

```python
"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the full HivePlane schema and its indexes."""
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """Drop the full HivePlane schema."""
    Base.metadata.drop_all(bind=op.get_bind())
```

(The initial revision deliberately derives from `Base.metadata` so the schema cannot drift
from the models; `env.py` wires `target_metadata` so future `alembic revision --autogenerate`
works.)

- [ ] **Step 5: Run tests to verify they pass (with Postgres)**

Run: `docker compose up -d postgres && .venv/bin/python -m pytest tests/test_persistence_migrations.py -q`
Expected: PASS (2 tests). Without Postgres: SKIPPED.

- [ ] **Step 6: Commit**

```bash
git add alembic.ini src/hiveplane/persistence/migrations tests/postgres.py tests/test_persistence_migrations.py
git commit -m "feat(persistence): add alembic migrations and schema"
```

---

### Task 4: PostgresRunStore

**Files:**
- Create: `src/hiveplane/persistence/run_store.py`
- Test: `tests/test_persistence_run_store.py`

**Interfaces:**
- Consumes: all `RunStore`/`RunBundle` models, `RunNotFoundError`, `Base`, `models`.
- Produces: `PostgresRunStore(engine)` implementing `RunStore` (same semantics as `InMemoryRunStore`: copies on read, ordered events, `RunNotFoundError` on unknown run).

- [ ] **Step 1: Write the failing test**

`tests/test_persistence_run_store.py`:

```python
"""PostgresRunStore round-trip and durability tests (Postgres-gated)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import AdmissionOutcome, AdmissionResult
from hiveplane.persistence.base import create_engine_from_settings
from hiveplane.persistence.run_store import PostgresRunStore
from postgres import pg_engine  # noqa: F401

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(state: RunState = RunState.PAUSED) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=state,
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.PRODUCTION,
        sandbox=True,
    )


def test_round_trip(pg_engine: Engine) -> None:
    store = PostgresRunStore(pg_engine)
    store.save_run(_run())
    store.add_event(
        RunEvent(
            run_id="run-1",
            sequence=0,
            type=EventType.STATE_CHANGE,
            actor="operator",
            timestamp=_NOW,
            from_state=RunState.RUNNING,
            to_state=RunState.PAUSED,
        )
    )
    store.add_usage(
        UsageReport(
            run_id="run-1",
            input_tokens=10,
            output_tokens=5,
            tool_calls=1,
            cost_usd=0.02,
            timestamp=_NOW,
        )
    )
    store.save_admission(
        AdmissionResult(
            run_id="run-1",
            workload="agent-1",
            context=AdmissionContext.PRODUCTION,
            outcome=AdmissionOutcome.ADMITTED,
        )
    )

    assert store.get_run("run-1").state is RunState.PAUSED  # type: ignore[union-attr]
    assert store.list_events("run-1")[0].to_state is RunState.PAUSED
    assert store.list_usage("run-1")[0].total_tokens == 15
    assert store.get_admission("run-1").outcome is AdmissionOutcome.ADMITTED  # type: ignore[union-attr]
    assert {run.id for run in store.list_runs(workload="agent-1")} == {"run-1"}


def test_unknown_run_raises(pg_engine: Engine) -> None:
    store = PostgresRunStore(pg_engine)
    with pytest.raises(RunNotFoundError):
        store.list_events("missing")


def test_paused_run_survives_restart(pg_engine: Engine) -> None:
    PostgresRunStore(pg_engine).save_run(_run(state=RunState.PAUSED))

    fresh_engine = create_engine_from_settings()
    try:
        reopened = PostgresRunStore(fresh_engine)
        assert reopened.get_run("run-1").state is RunState.PAUSED  # type: ignore[union-attr]
    finally:
        fresh_engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence_run_store.py -q`
Expected: FAIL/SKIP — `ModuleNotFoundError: hiveplane.persistence.run_store`.

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/persistence/run_store.py`:

```python
"""PostgreSQL-backed RunStore (M18, DD-05)."""

from __future__ import annotations

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from hiveplane.core.event import RunEvent
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import AdmissionResult, DeliveryRecord
from hiveplane.persistence.base import session_factory
from hiveplane.persistence.models import (
    FanOutDeliveryRow,
    RunAdmissionRow,
    RunEventRow,
    RunRow,
    UsageEventRow,
)


class PostgresRunStore:
    """A durable run store backed by PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_run(self, run: Run) -> None:
        """Insert or update a run."""
        with self._session.begin() as session:
            row = session.get(RunRow, run.id)
            if row is None:
                session.add(
                    RunRow(
                        id=run.id,
                        workload_id=run.workload_id,
                        caller=run.caller,
                        state=run.state.value,
                        context=run.context.value if run.context else None,
                        model_identity=run.model_identity,
                        sandbox=run.sandbox,
                        sandbox_id=run.sandbox_id,
                        manifest_version=run.manifest_version,
                        cost_usd=run.cost_usd,
                        created_at=run.created_at,
                        updated_at=run.updated_at,
                        payload=run.model_dump(mode="json"),
                    )
                )
            else:
                row.workload_id = run.workload_id
                row.state = run.state.value
                row.context = run.context.value if run.context else None
                row.model_identity = run.model_identity
                row.sandbox = run.sandbox
                row.sandbox_id = run.sandbox_id
                row.manifest_version = run.manifest_version
                row.cost_usd = run.cost_usd
                row.updated_at = run.updated_at
                row.payload = run.model_dump(mode="json")

    def get_run(self, run_id: str) -> Run | None:
        """Return a run by id, or None."""
        with self._session() as session:
            row = session.get(RunRow, run_id)
            return Run.model_validate(row.payload) if row is not None else None

    def list_runs(
        self, *, workload: str | None = None, state: RunState | None = None
    ) -> list[Run]:
        """List runs filtered by workload and/or state, oldest first."""
        statement = select(RunRow).order_by(RunRow.created_at)
        if workload is not None:
            statement = statement.where(RunRow.workload_id == workload)
        if state is not None:
            statement = statement.where(RunRow.state == state.value)
        with self._session() as session:
            return [Run.model_validate(row.payload) for row in session.scalars(statement)]

    def add_event(self, event: RunEvent) -> None:
        """Append one run event."""
        with self._session.begin() as session:
            self._require(session, event.run_id)
            session.add(
                RunEventRow(
                    run_id=event.run_id,
                    sequence=event.sequence,
                    type=event.type.value,
                    actor=event.actor,
                    timestamp=event.timestamp,
                    payload=event.model_dump(mode="json"),
                )
            )

    def list_events(self, run_id: str) -> list[RunEvent]:
        """Return events ordered by sequence."""
        with self._session() as session:
            self._require(session, run_id)
            rows = session.scalars(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id)
                .order_by(RunEventRow.sequence)
            )
            return [RunEvent.model_validate(row.payload) for row in rows]

    def add_usage(self, report: UsageReport) -> None:
        """Append a usage report."""
        with self._session.begin() as session:
            self._require(session, report.run_id)
            session.add(
                UsageEventRow(
                    run_id=report.run_id,
                    model_identity=report.model_identity,
                    cost_usd=report.cost_usd,
                    timestamp=report.timestamp,
                    payload=report.model_dump(mode="json"),
                )
            )

    def list_usage(self, run_id: str) -> list[UsageReport]:
        """Return usage reports for a run."""
        with self._session() as session:
            self._require(session, run_id)
            rows = session.scalars(
                select(UsageEventRow)
                .where(UsageEventRow.run_id == run_id)
                .order_by(UsageEventRow.id)
            )
            return [UsageReport.model_validate(row.payload) for row in rows]

    def save_admission(self, result: AdmissionResult) -> None:
        """Insert or replace a run's admission decision."""
        with self._session.begin() as session:
            row = session.get(RunAdmissionRow, result.run_id)
            if row is None:
                session.add(
                    RunAdmissionRow(
                        run_id=result.run_id,
                        outcome=result.outcome.value,
                        context=result.context.value,
                        payload=result.model_dump(mode="json"),
                    )
                )
            else:
                row.outcome = result.outcome.value
                row.context = result.context.value
                row.payload = result.model_dump(mode="json")

    def get_admission(self, run_id: str) -> AdmissionResult | None:
        """Return a run's admission decision, or None."""
        with self._session() as session:
            row = session.get(RunAdmissionRow, run_id)
            return AdmissionResult.model_validate(row.payload) if row is not None else None

    def add_delivery(self, record: DeliveryRecord) -> None:
        """Append a fan-out delivery record."""
        with self._session.begin() as session:
            self._require(session, record.run_id)
            session.add(
                FanOutDeliveryRow(
                    delivery_id=(
                        f"{record.run_id}:{record.destination_type.value}:"
                        f"{record.timestamp.isoformat()}"
                    ),
                    run_id=record.run_id,
                    destination_type=record.destination_type.value,
                    status=record.status.value,
                    timestamp=record.timestamp,
                    payload=record.model_dump(mode="json"),
                )
            )

    def list_deliveries(self, run_id: str) -> list[DeliveryRecord]:
        """Return delivery records for a run."""
        with self._session() as session:
            self._require(session, run_id)
            rows = session.scalars(
                select(FanOutDeliveryRow)
                .where(FanOutDeliveryRow.run_id == run_id)
                .order_by(FanOutDeliveryRow.timestamp)
            )
            return [DeliveryRecord.model_validate(row.payload) for row in rows]

    @staticmethod
    def _require(session: Session, run_id: str) -> None:
        if session.get(RunRow, run_id) is None:
            raise RunNotFoundError(run_id)

    def clear(self) -> None:
        """Delete all run data; used by tests and destructive operations."""
        with self._session.begin() as session:
            for table in (
                FanOutDeliveryRow,
                UsageEventRow,
                RunEventRow,
                RunAdmissionRow,
                RunRow,
            ):
                session.execute(delete(table))
```

Notes:
- Events rely on the caller's unique `sequence` per run; the pair `(run_id, sequence)` is the PK and a duplicate insert raises, matching append-only semantics.
- `delivery_id` derives from `(run_id, destination_type, timestamp)` because `DeliveryRecord` carries no id.
- `clear()` deletes run data in FK order; it is used by tests and destructive operations.

- [ ] **Step 4: Run tests to verify they pass (with Postgres)**

Run: `.venv/bin/python -m pytest tests/test_persistence_run_store.py -q`
Expected: PASS (3 tests) with Postgres; SKIPPED without.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/persistence/run_store.py tests/test_persistence_run_store.py
git commit -m "feat(persistence): add PostgresRunStore"
```

---

### Task 5: Tamper-evident audit chain

**Files:**
- Create: `src/hiveplane/persistence/audit.py`
- Test: `tests/test_persistence_audit.py`

**Interfaces:**
- Consumes: stdlib `hashlib`, `json`, `datetime`.
- Produces:
  - `AuditRecord` (pydantic: `sequence`, `actor`, `action`, `subject`, `created_at`, `prev_hash`, `hash`, `detail`)
  - `AuditLog` Protocol: `append(actor, action, subject, *, detail=None) -> AuditRecord`, `records() -> list[AuditRecord]`, `verify() -> bool`
  - `AuditChain` with static `compute_hash(prev_hash, record_payload) -> str` and `verify(records) -> int | None` (index of first break)
  - `InMemoryAuditLog`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the tamper-evident audit chain (no database required)."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.persistence.audit import AuditChain, InMemoryAuditLog

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_append_chains_hashes() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    first = log.append("operator", "pause", "run-1")
    second = log.append("operator", "resume", "run-1")
    assert first.prev_hash == "0" * 64
    assert second.prev_hash == first.hash
    assert second.hash != first.hash


def test_verify_detects_tampering() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    log.append("operator", "pause", "run-1")
    log.append("operator", "stop", "run-1")
    assert AuditChain.verify(log.records()) is None

    records = log.records()
    records[0] = records[0].model_copy(update={"detail": "tampered"})
    assert AuditChain.verify(records) == 0


def test_verify_detects_hash_mismatch() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    record = log.append("operator", "pause", "run-1")
    broken = record.model_copy(update={"hash": "f" * 64})
    assert AuditChain.verify([broken]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence_audit.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.persistence.audit'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/persistence/audit.py`:

```python
"""Tamper-evident audit log (M18, D7).

Each record's hash covers the previous hash plus the record's canonical payload,
so any edited, reordered, or deleted record breaks verification at that index.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

_NULL_HASH = "0" * 64


class AuditRecord(BaseModel):
    """One tamper-evident audit entry."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    created_at: AwareDatetime
    detail: str | None = None
    prev_hash: str
    hash: str


class AuditLog(Protocol):
    """Append-only, tamper-evident audit log."""

    def append(
        self, actor: str, action: str, subject: str, *, detail: str | None = None
    ) -> AuditRecord: ...

    def records(self) -> list[AuditRecord]: ...

    def verify(self) -> bool: ...


def _canonical(record: AuditRecord) -> str:
    payload = {
        "sequence": record.sequence,
        "actor": record.actor,
        "action": record.action,
        "subject": record.subject,
        "created_at": record.created_at.isoformat(),
        "detail": record.detail,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class AuditChain:
    """Chained-hash math shared by audit backends."""

    @staticmethod
    def compute_hash(prev_hash: str, record: AuditRecord) -> str:
        """Return the hash for ``record`` given the previous link."""
        digest = hashlib.sha256(f"{prev_hash}|{_canonical(record)}".encode())
        return digest.hexdigest()

    @staticmethod
    def verify(records: list[AuditRecord]) -> int | None:
        """Return the index of the first broken link, or None when intact."""
        prev = _NULL_HASH
        for index, record in enumerate(records):
            if record.prev_hash != prev:
                return index
            if record.hash != AuditChain.compute_hash(prev, record):
                return index
            prev = record.hash
        return None


class InMemoryAuditLog:
    """An in-process tamper-evident audit log."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: list[AuditRecord] = []

    def append(
        self, actor: str, action: str, subject: str, *, detail: str | None = None
    ) -> AuditRecord:
        """Append an audit entry, linking it to the previous hash."""
        prev_hash = self._records[-1].hash if self._records else _NULL_HASH
        draft = AuditRecord(
            sequence=len(self._records),
            actor=actor,
            action=action,
            subject=subject,
            created_at=self._clock(),
            detail=detail,
            prev_hash=prev_hash,
            hash="",
        )
        record = draft.model_copy(update={"hash": AuditChain.compute_hash(prev_hash, draft)})
        self._records.append(record)
        return record

    def records(self) -> list[AuditRecord]:
        """Return a copy of the audit records."""
        return [record.model_copy(deep=True) for record in self._records]

    def verify(self) -> bool:
        """Return True when the chain is intact."""
        return AuditChain.verify(self._records) is None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persistence_audit.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/persistence/audit.py tests/test_persistence_audit.py
git commit -m "feat(persistence): add tamper-evident audit chain"
```

---

### Task 6: Postgres audit log and RunService wiring

**Files:**
- Create: `src/hiveplane/persistence/postgres_audit.py`
- Modify: `src/hiveplane/execution/service.py`
- Test: `tests/test_persistence_postgres_audit.py`, `tests/test_execution_audit.py`

**Interfaces:**
- Consumes: `AuditRow`, `AuditChain`, `AuditRecord`, `AuditLog`, `RunService`, `InterventionAction`, `RunState`.
- Produces:
  - `PostgresAuditLog(engine, *, clock=None)` implementing `AuditLog`.
  - `RunService(..., audit: AuditLog | None = None)`; appends audit entries on interventions and terminal transitions when configured.

- [ ] **Step 1: Write the failing tests**

`tests/test_persistence_postgres_audit.py`:

```python
"""PostgresAuditLog persistence and tamper detection (Postgres-gated)."""

from __future__ import annotations

from sqlalchemy import Engine, text

from hiveplane.persistence.postgres_audit import PostgresAuditLog
from postgres import pg_engine  # noqa: F401


def test_audit_persists_and_verifies(pg_engine: Engine) -> None:
    log = PostgresAuditLog(pg_engine)
    log.append("operator", "pause", "run-1")
    log.append("operator", "stop", "run-1")

    reopened = PostgresAuditLog(pg_engine)
    assert [record.action for record in reopened.records()] == ["pause", "stop"]
    assert reopened.verify() is True


def test_tampering_is_detected(pg_engine: Engine) -> None:
    log = PostgresAuditLog(pg_engine)
    log.append("operator", "pause", "run-1")
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE audit_log SET actor = 'intruder'"))

    reopened = PostgresAuditLog(pg_engine)
    assert reopened.verify() is False
```

`tests/test_execution_audit.py`:

```python
"""Tests that RunService records audit entries when an audit log is configured."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


class _FanOut:
    def notify(self, run: object, workload: object) -> list[object]:
        return []

    def notify_escalation(self, run: object, workload: object) -> list[object]:
        return []


def _service(make_manifest: Callable[..., AgentWorkload], audit: InMemoryAuditLog) -> RunService:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(make_manifest(name="agent-1"))
    return RunService(
        InMemoryRunStore(),
        registry,
        admission=AdmissionPipeline(_Cert(True), _Policy(DecisionOutcome.ALLOW), _Budget(True), _Sandbox(False)),
        executor=None,
        fanout=_FanOut(),
        audit=audit,
    )


def test_intervention_is_audited(make_manifest: Callable[..., AgentWorkload]) -> None:
    audit = InMemoryAuditLog(clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    service = _service(make_manifest, audit)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    service.start(run.id, actor="cli")
    service.intervene(run.id, InterventionAction.PAUSE, actor="operator")

    actions = [record.action for record in audit.records()]
    assert "pause" in actions
    assert audit.verify() is True


def test_terminal_transition_is_audited(make_manifest: Callable[..., AgentWorkload]) -> None:
    audit = InMemoryAuditLog()
    service = _service(make_manifest, audit)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    service.start(run.id, actor="cli")
    service.transition(run.id, RunState.COMPLETED, actor="adapter")

    assert any(record.action == "transition" for record in audit.records())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_persistence_postgres_audit.py tests/test_execution_audit.py -q`
Expected: FAIL/SKIP — missing module; `RunService.__init__` has no `audit`.

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/persistence/postgres_audit.py`:

```python
"""PostgreSQL-backed tamper-evident audit log (M18)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, select

from hiveplane.persistence.audit import AuditChain, AuditRecord
from hiveplane.persistence.base import session_factory
from hiveplane.persistence.models import AuditRow


class PostgresAuditLog:
    """A durable, tamper-evident audit log backed by PostgreSQL."""

    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session = session_factory(engine)

    def append(
        self, actor: str, action: str, subject: str, *, detail: str | None = None
    ) -> AuditRecord:
        """Append a linked audit entry."""
        with self._session.begin() as session:
            last = session.scalars(
                select(AuditRow).order_by(AuditRow.sequence.desc()).limit(1)
            ).first()
            prev_hash = last.hash if last is not None else "0" * 64
            sequence = last.sequence + 1 if last is not None else 1
            draft = AuditRecord(
                sequence=sequence,
                actor=actor,
                action=action,
                subject=subject,
                created_at=self._clock(),
                detail=detail,
                prev_hash=prev_hash,
                hash="",
            )
            record = draft.model_copy(
                update={"hash": AuditChain.compute_hash(prev_hash, draft)}
            )
            session.add(
                AuditRow(
                    sequence=record.sequence,
                    actor=record.actor,
                    action=record.action,
                    subject=record.subject,
                    created_at=record.created_at,
                    prev_hash=record.prev_hash,
                    hash=record.hash,
                    payload=record.model_dump(mode="json"),
                )
            )
            return record

    def records(self) -> list[AuditRecord]:
        """Return all audit records in chain order."""
        with self._session() as session:
            rows = session.scalars(select(AuditRow).order_by(AuditRow.sequence))
            return [AuditRecord.model_validate(row.payload) for row in rows]

    def verify(self) -> bool:
        """Return True when the persisted chain is intact."""
        return AuditChain.verify(self.records()) is None
```

`src/hiveplane/execution/service.py` changes:
1. Import `AuditLog`:

```python
from hiveplane.persistence.audit import AuditLog
```

2. Add the constructor parameter (keyword-only, default `None`):

```python
        audit: AuditLog | None = None,
```

and store it:

```python
        self._audit = audit
```

3. In `intervene`, after each successful action, append an audit entry (place after the state
   transition for pause/resume/stop):

```python
        if self._audit is not None:
            self._audit.append(actor, action.value, run_id)
```

4. In `transition`, when the target is terminal, append an audit entry:

```python
        if self._audit is not None and target in _TERMINAL:
            self._audit.append(actor, "transition", run_id, detail=target.value)
```

- [ ] **Step 4: Run tests to verify they pass (with Postgres for the first file)**

Run: `.venv/bin/python -m pytest tests/test_execution_audit.py -q` (local)
Run: `.venv/bin/python -m pytest tests/test_persistence_postgres_audit.py -q` (with Postgres)
Expected: PASS (2 + 2 tests); the Postgres file SKIPS without a database.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/persistence/postgres_audit.py src/hiveplane/execution/service.py tests/test_persistence_postgres_audit.py tests/test_execution_audit.py
git commit -m "feat(persistence): add Postgres audit log and run-service audit wiring"
```

---

### Task 7: Wiring, CI, docs, and gate

**Files:**
- Modify: `src/hiveplane/execution/wiring.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/design/state-store-design.md`, WBS files, `docs/USER_GUIDE.md`
- Test: `tests/test_persistence_wiring.py`

**Interfaces:**
- Consumes: `ExecutionSettings.store`, `create_engine_from_settings`, `PostgresRunStore`, `PostgresAuditLog`.
- Produces: `build_run_store()` returns `PostgresRunStore` when `store == "postgres"`; `build_audit_log()` returns an audit log (Postgres or in-memory).

- [ ] **Step 1: Write the failing test**

```python
"""Tests for persistence wiring."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.wiring import build_run_store
from postgres import pg_engine  # noqa: F401

_ROOT = Path(__file__).resolve().parents[1]


def test_memory_store_selection() -> None:
    assert isinstance(build_run_store(store="memory"), InMemoryRunStore)


def test_postgres_store_selection(pg_engine: Engine) -> None:
    from hiveplane.persistence.run_store import PostgresRunStore

    store = build_run_store(store="postgres")
    assert isinstance(store, PostgresRunStore)
    store.clear()


def test_ci_has_a_postgres_service() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "postgres:16" in workflow
    assert "alembic upgrade head" in workflow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence_wiring.py -q`
Expected: FAIL — `build_run_store` takes no `store` argument; no CI Postgres.

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/execution/wiring.py`:

```python
def build_run_store(store: str | None = None) -> RunStore:
    """Build the configured run store (durable JSON by default)."""
    settings = get_settings()
    selected = store or settings.execution.store
    if selected == "postgres":
        return PostgresRunStore(create_engine_from_settings(settings))
    if selected == "json":
        return JsonFileRunStore(settings.execution.data_dir)
    return InMemoryRunStore()


def build_audit_log() -> AuditLog:
    """Build the configured audit log (Postgres when the store is Postgres)."""
    settings = get_settings()
    if settings.execution.store == "postgres":
        return PostgresAuditLog(create_engine_from_settings(settings))
    return InMemoryAuditLog()
```

Add the imports (`PostgresRunStore`, `create_engine_from_settings`, `PostgresAuditLog`, `InMemoryAuditLog`,
`AuditLog`). Note `build_run_service` should pass `audit=build_audit_log()` only when the Postgres
store is selected (to avoid a fresh in-memory audit being discarded); simplest: always pass
`audit=None` unless `settings.execution.store == "postgres"`.

`.github/workflows/ci.yml` — add a service and a migration step:

```yaml
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: hiveplane
          POSTGRES_USER: hiveplane
          POSTGRES_PASSWORD: hiveplane
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U hiveplane -d hiveplane"
          --health-interval 5s --health-timeout 5s --health-retries 10
```

Add an env block and a step before the test run:

```yaml
      - name: Migrate database
        env:
          HIVEPLANE_DATABASE__HOST: localhost
        run: alembic upgrade head
```

(Set `HIVEPLANE_DATABASE__HOST: localhost` on the migration and pytest steps. `alembic` is on
`PATH` after `pip install -e ".[dev,langgraph]"` because it is a core dependency.)

- [ ] **Step 4: Run test to verify it passes (with Postgres for the first test)**

Run: `.venv/bin/python -m pytest tests/test_persistence_wiring.py -q`
Expected: PASS (3 tests) with Postgres; the Postgres-selection test SKIPS without a database via the
`pg_engine` fixture.

- [ ] **Step 5: Update docs and WBS**

- `docs/design/state-store-design.md`: change `> Status: draft` to `> Status: implemented (M18); run and audit tables live, other entities schema-only.`
- `docs/wbs/v0.1.0/wbs-v0.1.0-part9-state-store.md`: check #46/#47 and mark the M18 exit gate with evidence.
- `docs/wbs/v0.1.0/wbs-v0.1.0-index.md`: update progress to 46/60 and drop state store from "remain".
- `docs/USER_GUIDE.md`: add a short "Persistence" note (`HIVEPLANE_EXECUTION__STORE=postgres`, run migrations with `alembic upgrade head`).

- [ ] **Step 6: Run the full gate**

Run locally: `.venv/bin/ruff check && .venv/bin/mypy src/ tests/ && .venv/bin/python -m pytest -q --cov=src/hiveplane --cov-report=term`
Then with Postgres: `docker compose up -d postgres && .venv/bin/python -m pytest -q`
Expected: ruff + mypy clean; coverage > 95%; all tests pass (DB tests run with Postgres, skip without).

- [ ] **Step 7: Commit**

```bash
git add src/hiveplane/execution/wiring.py .github/workflows/ci.yml docs/design/state-store-design.md docs/wbs/v0.1.0/wbs-v0.1.0-part9-state-store.md docs/wbs/v0.1.0/wbs-v0.1.0-index.md docs/USER_GUIDE.md tests/test_persistence_wiring.py
git commit -m "feat(persistence): wire postgres store, CI service, and docs"
```

---

## Self-Review

**Spec coverage (#46, #47):**
- All 15 design tables + `run_admissions`, with the design's indexes → Task 2.
- Alembic migrations apply/roll back cleanly → Task 3.
- Query plans use the intended indexes → Task 3 (`EXPLAIN`).
- Transitions persisted before side effects → existing `RunService` ordering (persist-then-side-effect) + Postgres store → Tasks 4/6.
- Append-only event log → `(run_id, sequence)` PK; audit chained hash → Tasks 4/5.
- Retention and backup/restore → documented in `state-store-design.md` (Task 7); no code needed for v0.1.0.
- Kill-and-restart preserves a paused run → Task 4.
- Audit tamper detection → Tasks 5/6.

**Type consistency:** `PostgresRunStore` implements the exact `RunStore` protocol (Task 4);
`RunService(audit=AuditLog | None)` uses only `append`, defined in `AuditLog` (Task 5);
`PostgresAuditLog` implements the same `AuditLog` (Task 6). `build_run_store(store=...)` and
`build_audit_log()` are consumed only by wiring/tests (Task 7).

**Known simplifications (documented):** only runs/audit are live in M18; other tables are
schema-only and their stores remain in-memory/JSON. Retention/backup are documented policies, not
automated jobs. Coverage omits live-DB glue and migrations; those paths are covered by gated tests
in CI, not by the local percentage.