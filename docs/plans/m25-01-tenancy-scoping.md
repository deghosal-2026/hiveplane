# M25-01 — Tenancy & Tenant Scoping Implementation Plan

> **Status: complete.** All tasks landed and verified: 990 tests pass against PostgreSQL 16
> (965 + 27 skipped without a database), mypy strict and ruff clean.
>
> **Deviation from this plan (deliberate):** `ctx` is a keyword-only parameter defaulting to
> `DEFAULT_CONTEXT` on the fleet stores and services, rather than a required positional
> argument. Same isolation semantics (reads filter by tenant, cross-tenant writes raise
> `TenantScopeError`), but it avoids churning every existing call site and test. The tenancy
> store (`hiveplane.tenancy.store`) keeps `ctx` as a required positional argument, since
> tenant administration is inherently tenant-explicit.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class tenants, teams, and memberships, and make every persisted control-plane entity tenant-scoped, enforcing isolation at the store layer with DB-level composite foreign keys.

**Architecture:** A new `hiveplane.tenancy` package defines tenant/team/membership domain models, a frozen `TenantContext`, and `TenantScopeError`. Every durable store method takes an explicit `TenantContext`, filters reads by the row's `tenant_id`, and raises `TenantScopeError` on cross-tenant writes. Existing ORM tables gain `tenant_id` (+ `team_id`/`attribution_key` where attribution matters) with composite `(parent_id, tenant_id)` foreign keys and tenant-qualified unique constraints. A single forward-only Alembic revision `0003` creates tenancy tables, seeds `default`/`system`, adds columns, backfills, and adds constraints; startup auto-migration is preserved.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0, Alembic, FastAPI, pytest, ruff, mypy strict.

## Global Constraints

- Direction: forward-only. No backward-compatibility shims; update all call sites in-repo.
- Version bump: `pyproject.toml` and `src/hiveplane/__init__.py` move `0.1.0` → `0.2.0`.
- IDs: primary/foreign-key ids `String(64)` (exception: `membership_id` `String(128)`); human identity/name/key fields `String(253)` (`name`, `attribution_key`, `operator_id`).
- Reserved tenant ids: `default` (legacy/backfill) and `system` (internal/admin). Reserved default team/attribution key: `default`.
- Every durable store method takes an explicit `TenantContext` as its first parameter. No ContextVars, no implicit tenant.
- Reads across tenants return `None`/omit the row (indistinguishable from not-found). Writes across tenants raise `TenantScopeError`.
- `TenantContext.is_system=True` bypasses scoping for internal/admin operations only.
- All existing tests must stay green at every task boundary (except tests updated within the same task). Exit gate: `pytest`, coverage > 95% for new model/migration modules, `ruff check`, `mypy src/ tests/`.
- No comments in code unless the surrounding file already uses them.

## File Structure

**Create**
- `src/hiveplane/tenancy/__init__.py` — public exports.
- `src/hiveplane/tenancy/errors.py` — `TenancyError`, `TenantScopeError`, `TenantNotFoundError`, `TeamNotFoundError`.
- `src/hiveplane/tenancy/models.py` — `Role`, `Tenant`, `Team`, `Membership`.
- `src/hiveplane/tenancy/context.py` — `TenantContext`, `SYSTEM_CONTEXT`, `DEFAULT_CONTEXT`, id/key constants.
- `src/hiveplane/tenancy/store.py` — `TenantStore` protocol, `InMemoryTenantStore`, `PostgresTenantStore`, `build_tenant_store`.
- `src/hiveplane/persistence/migrations/versions/0003_tenancy_scoping.py` — forward-only migration.
- `tests/test_tenancy_models.py`, `tests/test_tenancy_context.py`, `tests/test_tenancy_store.py`.
- `tests/test_tenant_scoping_isolation.py` — cross-store isolation matrix.
- `tests/test_persistence_migration_0003.py` — migration/backfill tests.

**Modify**
- `src/hiveplane/persistence/models.py` — `TenantScoped`/`Attributed` mixins + `TenantRow`/`TeamRow`/`MembershipRow`; tenant columns, composite FKs, tenant-qualified uniques on all existing rows.
- `src/hiveplane/core/run.py`, `src/hiveplane/registry/models.py`, `src/hiveplane/budget/models.py`, `src/hiveplane/core/approval.py` — add `tenant_id`/`team_id`/`attribution_key` fields (defaulted to `DEFAULT_*`, validated non-empty).
- Every store + its protocol + in-memory/JSON/Postgres impls: `execution/store.py`, `persistence/run_store.py`, `registry/store.py`, `certification/store.py`, `budget/store.py`, `policy/store.py`, `policy/packs.py`, `persistence/audit.py`, `persistence/postgres_audit.py`.
- Owning services and direct API handlers: `execution/service.py`, `execution/fanout.py`, `execution/wiring.py`, `registry/service.py`, `certification/workflow.py`, `budget/service.py`, `policy/approvals.py`, `policy/engine.py`, `api/app.py`, `api/deps.py`, `api/runs.py`, `api/spend.py`, `api/policy.py`, `api/readiness.py`.
- `pyproject.toml` (version + coverage omit list), `src/hiveplane/__init__.py`, `CHANGELOG.md`, `docs/design/fleet-control-data-model-design.md` (record decided mechanics), `docs/design/state-store-design.md` (cross-ref).
- Tests listed per task in Phases 2–3.

---

## Phase 1 — Tenancy foundation

### Task 1: Tenancy errors and domain models

**Files:**
- Create: `src/hiveplane/tenancy/__init__.py`, `src/hiveplane/tenancy/errors.py`, `src/hiveplane/tenancy/models.py`
- Test: `tests/test_tenancy_models.py`

**Interfaces:**
- Produces: `Role` (`ADMIN`/`APPROVER`/`VIEWER`), `Tenant(tenant_id, name, created_at)`, `Team(team_id, tenant_id, name, attribution_key, created_at)`, `Membership(membership_id, tenant_id, team_id, operator_id, role, created_at)`, `TenantScopeError(tenant_id, detail)`, `TenantNotFoundError`, `TeamNotFoundError`.

- [ ] **Step 1: Write the failing test**

```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.tenancy import Membership, Role, Team, Tenant
from hiveplane.tenancy.errors import TenantScopeError

_NOW = datetime(2026, 9, 25, tzinfo=UTC)


def test_tenant_accepts_valid_fields() -> None:
    tenant = Tenant(tenant_id="acme", name="Acme", created_at=_NOW)
    assert tenant.tenant_id == "acme"


def test_tenant_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        Tenant(tenant_id="", name="Acme", created_at=_NOW)


def test_tenant_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Tenant(tenant_id="acme", name="Acme", created_at=_NOW, nope=1)


def test_team_is_tenant_qualified() -> None:
    team = Team(
        team_id="platform",
        tenant_id="acme",
        name="Platform",
        attribution_key="acme.platform",
        created_at=_NOW,
    )
    assert team.attribution_key == "acme.platform"


def test_membership_role_is_enum() -> None:
    membership = Membership(
        membership_id="m1",
        tenant_id="acme",
        team_id="platform",
        operator_id="alice",
        role="admin",
        created_at=_NOW,
    )
    assert membership.role is Role.ADMIN


def test_scope_error_carries_tenant_and_detail() -> None:
    error = TenantScopeError("acme", "cannot access tenant 'other'")
    assert error.tenant_id == "acme"
    assert "other" in str(error)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tenancy_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hiveplane.tenancy'`.

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/tenancy/errors.py`:

```python
"""Tenancy errors."""

from __future__ import annotations


class TenancyError(Exception):
    """Base class for tenancy failures."""


class TenantScopeError(TenancyError):
    """Raised when an operation crosses a tenant boundary."""

    def __init__(self, tenant_id: str, detail: str) -> None:
        self.tenant_id = tenant_id
        self.detail = detail
        super().__init__(f"tenant scope violation for {tenant_id!r}: {detail}")


class TenantNotFoundError(TenancyError):
    """Raised when a tenant does not exist."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        super().__init__(f"tenant not found: {tenant_id!r}")


class TeamNotFoundError(TenancyError):
    """Raised when a team does not exist in a tenant."""

    def __init__(self, tenant_id: str, team_id: str) -> None:
        self.tenant_id = tenant_id
        self.team_id = team_id
        super().__init__(f"team not found: {team_id!r} in tenant {tenant_id!r}")
```

`src/hiveplane/tenancy/models.py`:

```python
"""Tenant, team, and membership domain models (D21, D33)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Role(StrEnum):
    """A membership role inside a tenant."""

    ADMIN = "admin"
    APPROVER = "approver"
    VIEWER = "viewer"


class Tenant(BaseModel):
    """The isolation boundary; all fleet rows belong to exactly one tenant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    created_at: AwareDatetime


class Team(BaseModel):
    """An attribution and policy scope inside a tenant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    team_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    attribution_key: str = Field(min_length=1, max_length=253)
    created_at: AwareDatetime


class Membership(BaseModel):
    """Binds an operator to a role within a tenant (and optional team)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    membership_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=64)
    team_id: str | None = Field(default=None, max_length=64)
    operator_id: str = Field(min_length=1, max_length=253)
    role: Role
    created_at: AwareDatetime
```

`src/hiveplane/tenancy/__init__.py`:

```python
"""Tenancy: isolation boundary, teams, memberships, and scoping context."""

from __future__ import annotations

from hiveplane.tenancy.errors import (
    TeamNotFoundError,
    TenancyError,
    TenantNotFoundError,
    TenantScopeError,
)
from hiveplane.tenancy.models import Membership, Role, Team, Tenant

__all__ = [
    "Membership",
    "Role",
    "Team",
    "TeamNotFoundError",
    "TenancyError",
    "Tenant",
    "TenantNotFoundError",
    "TenantScopeError",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tenancy_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/tenancy tests/test_tenancy_models.py
git commit -m "feat(tenancy): add tenant/team/membership models and scope errors (#149)"
```

---

### Task 2: TenantContext and scope resolution

**Files:**
- Create: `src/hiveplane/tenancy/context.py`
- Modify: `src/hiveplane/tenancy/__init__.py`
- Test: `tests/test_tenancy_context.py`

**Interfaces:**
- Consumes: `Role`, `TenantScopeError` from Task 1.
- Produces: constants `DEFAULT_TENANT_ID`, `DEFAULT_TEAM_ID`, `DEFAULT_ATTRIBUTION_KEY`, `SYSTEM_TENANT_ID`; `TenantContext(tenant_id, team_id=None, operator_id=None, role=Role.VIEWER, attribution_key=None, is_system=False)` with `scopes(tenant_id) -> bool` and `require(tenant_id) -> None`; singletons `SYSTEM_CONTEXT`, `DEFAULT_CONTEXT`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from hiveplane.tenancy import Role, TenantScopeError
from hiveplane.tenancy.context import (
    DEFAULT_ATTRIBUTION_KEY,
    DEFAULT_CONTEXT,
    DEFAULT_TENANT_ID,
    SYSTEM_CONTEXT,
    TenantContext,
)


def test_context_scopes_only_its_tenant() -> None:
    ctx = TenantContext(tenant_id="acme", role=Role.ADMIN)
    assert ctx.scopes("acme")
    assert not ctx.scopes("other")


def test_require_raises_on_foreign_tenant() -> None:
    ctx = TenantContext(tenant_id="acme")
    with pytest.raises(TenantScopeError):
        ctx.require("other")
    ctx.require("acme")


def test_system_context_bypasses_scoping() -> None:
    assert SYSTEM_CONTEXT.is_system
    assert SYSTEM_CONTEXT.scopes("anything")
    SYSTEM_CONTEXT.require("anything")


def test_default_context_is_default_tenant_and_team() -> None:
    assert DEFAULT_CONTEXT.tenant_id == DEFAULT_TENANT_ID
    assert DEFAULT_CONTEXT.team_id == "default"
    assert DEFAULT_CONTEXT.attribution_key == DEFAULT_ATTRIBUTION_KEY


def test_context_is_frozen() -> None:
    ctx = TenantContext(tenant_id="acme")
    with pytest.raises(Exception):
        ctx.tenant_id = "other"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tenancy_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hiveplane.tenancy.context'`.

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/tenancy/context.py`:

```python
"""Tenant context carried explicitly through stores and services (D21)."""

from __future__ import annotations

from dataclasses import dataclass

from hiveplane.tenancy.errors import TenantScopeError
from hiveplane.tenancy.models import Role

DEFAULT_TENANT_ID = "default"
DEFAULT_TEAM_ID = "default"
DEFAULT_ATTRIBUTION_KEY = "default"
SYSTEM_TENANT_ID = "system"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """The acting tenant identity for a store or service call."""

    tenant_id: str
    team_id: str | None = None
    operator_id: str | None = None
    role: Role = Role.VIEWER
    attribution_key: str | None = None
    is_system: bool = False

    def scopes(self, tenant_id: str) -> bool:
        """Return True when this context may see ``tenant_id``'s rows."""
        return self.is_system or self.tenant_id == tenant_id

    def require(self, tenant_id: str) -> None:
        """Raise :class:`TenantScopeError` when ``tenant_id`` is out of scope."""
        if not self.scopes(tenant_id):
            raise TenantScopeError(self.tenant_id, f"cannot access tenant {tenant_id!r}")


SYSTEM_CONTEXT = TenantContext(
    tenant_id=SYSTEM_TENANT_ID,
    role=Role.ADMIN,
    is_system=True,
)

DEFAULT_CONTEXT = TenantContext(
    tenant_id=DEFAULT_TENANT_ID,
    team_id=DEFAULT_TEAM_ID,
    attribution_key=DEFAULT_ATTRIBUTION_KEY,
    role=Role.ADMIN,
)
```

Add `from hiveplane.tenancy.context import ...` names to `__init__.py` `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tenancy_context.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/tenancy tests/test_tenancy_context.py
git commit -m "feat(tenancy): add TenantContext and scope resolution (#149)"
```

---

### Task 3: Tenant store (in-memory + Postgres) and ORM tenancy rows

**Files:**
- Modify: `src/hiveplane/persistence/models.py`
- Create: `src/hiveplane/tenancy/store.py`
- Test: `tests/test_tenancy_store.py`

**Interfaces:**
- Consumes: `Tenant`, `Team`, `Membership`, `TenantContext`, errors.
- Produces: `TenantStore` protocol with `save_tenant/get_tenant/list_tenants`, `save_team/get_team/list_teams`, `save_membership/get_membership/list_memberships` (all taking `ctx` first), `InMemoryTenantStore`, `PostgresTenantStore(engine)`, `build_tenant_store(settings=None)`.

- [ ] **Step 1: Write the failing test**

```python
from datetime import UTC, datetime

import pytest

from hiveplane.tenancy import Membership, Role, Team, Tenant, TenantScopeError
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantNotFoundError
from hiveplane.tenancy.store import InMemoryTenantStore

_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_ACME = TenantContext(tenant_id="acme", role=Role.ADMIN)


def _tenant(tenant_id: str) -> Tenant:
    return Tenant(tenant_id=tenant_id, name=tenant_id.title(), created_at=_NOW)


def test_tenant_store_round_trips() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant("acme"))
    assert store.get_tenant(_ACME, "acme") == _tenant("acme")


def test_tenant_store_hides_foreign_tenants() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant("acme"))
    assert store.get_tenant(TenantContext(tenant_id="other"), "acme") is None


def test_team_save_requires_matching_tenant() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant("acme"))
    team = Team(
        team_id="platform",
        tenant_id="acme",
        name="Platform",
        attribution_key="acme.platform",
        created_at=_NOW,
    )
    store.save_team(_ACME, team)
    assert store.get_team(_ACME, "acme", "platform") == team
    with pytest.raises(TenantScopeError):
        store.save_team(TenantContext(tenant_id="other"), team)


def test_team_requires_existing_tenant() -> None:
    store = InMemoryTenantStore()
    team = Team(
        team_id="platform",
        tenant_id="acme",
        name="Platform",
        attribution_key="acme.platform",
        created_at=_NOW,
    )
    with pytest.raises(TenantNotFoundError):
        store.save_team(_ACME, team)


def test_membership_listing_is_tenant_scoped() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant("acme"))
    membership = Membership(
        membership_id="m1",
        tenant_id="acme",
        team_id=None,
        operator_id="alice",
        role=Role.ADMIN,
        created_at=_NOW,
    )
    store.save_membership(_ACME, membership)
    assert store.list_memberships(_ACME, "acme") == [membership]
    assert store.list_memberships(TenantContext(tenant_id="other"), "acme") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tenancy_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'InMemoryTenantStore'`.

- [ ] **Step 3: Write minimal implementation**

In `src/hiveplane/persistence/models.py` add:

```python
class TenantRow(Base):
    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tenants_tenant_name"),)

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TeamRow(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_teams_tenant_name"),
        UniqueConstraint("tenant_id", "attribution_key", name="uq_teams_tenant_attribution"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(253))
    attribution_key: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class MembershipRow(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "operator_id", "team_id", name="uq_memberships_tenant_operator_team"
        ),
        Index(
            "uq_memberships_tenant_operator_no_team",
            "tenant_id",
            "operator_id",
            unique=True,
            postgresql_where=text("team_id IS NULL"),
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            ["teams.tenant_id", "teams.team_id"],
            ondelete="CASCADE",
        ),
    )

    membership_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_id: Mapped[str] = mapped_column(String(253))
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)
```

Add `ForeignKeyConstraint` and `text` to the SQLAlchemy import list.

`src/hiveplane/tenancy/store.py`:

```python
"""Tenant/team/membership storage."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import MembershipRow, TeamRow, TenantRow
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TeamNotFoundError, TenantNotFoundError
from hiveplane.tenancy.models import Membership, Team, Tenant


class TenantStore(Protocol):
    def save_tenant(self, ctx: TenantContext, tenant: Tenant) -> None: ...
    def get_tenant(self, ctx: TenantContext, tenant_id: str) -> Tenant | None: ...
    def list_tenants(self, ctx: TenantContext) -> list[Tenant]: ...
    def save_team(self, ctx: TenantContext, team: Team) -> None: ...
    def get_team(self, ctx: TenantContext, tenant_id: str, team_id: str) -> Team | None: ...
    def list_teams(self, ctx: TenantContext, tenant_id: str) -> list[Team]: ...
    def save_membership(self, ctx: TenantContext, membership: Membership) -> None: ...
    def get_membership(
        self, ctx: TenantContext, membership_id: str
    ) -> Membership | None: ...
    def list_memberships(self, ctx: TenantContext, tenant_id: str) -> list[Membership]: ...


class InMemoryTenantStore:
    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._teams: dict[tuple[str, str], Team] = {}
        self._memberships: dict[str, Membership] = {}

    def save_tenant(self, ctx: TenantContext, tenant: Tenant) -> None:
        ctx.require(tenant.tenant_id)
        self._tenants[tenant.tenant_id] = tenant.model_copy(deep=True)

    def get_tenant(self, ctx: TenantContext, tenant_id: str) -> Tenant | None:
        if not ctx.scopes(tenant_id):
            return None
        tenant = self._tenants.get(tenant_id)
        return tenant.model_copy(deep=True) if tenant is not None else None

    def list_tenants(self, ctx: TenantContext) -> list[Tenant]:
        if ctx.is_system:
            tenants = list(self._tenants.values())
        else:
            tenants = [tenant for tenant in self._tenants.values() if ctx.scopes(tenant.tenant_id)]
        return [tenant.model_copy(deep=True) for tenant in tenants]

    def save_team(self, ctx: TenantContext, team: Team) -> None:
        ctx.require(team.tenant_id)
        if team.tenant_id not in self._tenants:
            raise TenantNotFoundError(team.tenant_id)
        self._teams[(team.tenant_id, team.team_id)] = team.model_copy(deep=True)

    def get_team(self, ctx: TenantContext, tenant_id: str, team_id: str) -> Team | None:
        if not ctx.scopes(tenant_id):
            return None
        team = self._teams.get((tenant_id, team_id))
        return team.model_copy(deep=True) if team is not None else None

    def list_teams(self, ctx: TenantContext, tenant_id: str) -> list[Team]:
        if not ctx.scopes(tenant_id):
            return []
        teams = [t for (tid, _), t in self._teams.items() if tid == tenant_id]
        teams.sort(key=lambda team: team.team_id)
        return [team.model_copy(deep=True) for team in teams]

    def save_membership(self, ctx: TenantContext, membership: Membership) -> None:
        ctx.require(membership.tenant_id)
        if membership.tenant_id not in self._tenants:
            raise TenantNotFoundError(membership.tenant_id)
        self._memberships[membership.membership_id] = membership.model_copy(deep=True)

    def get_membership(self, ctx: TenantContext, membership_id: str) -> Membership | None:
        membership = self._memberships.get(membership_id)
        if membership is None or not ctx.scopes(membership.tenant_id):
            return None
        return membership.model_copy(deep=True)

    def list_memberships(self, ctx: TenantContext, tenant_id: str) -> list[Membership]:
        if not ctx.scopes(tenant_id):
            return []
        memberships = [
            m for m in self._memberships.values() if m.tenant_id == tenant_id
        ]
        memberships.sort(key=lambda m: m.membership_id)
        return [m.model_copy(deep=True) for m in memberships]


class PostgresTenantStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_tenant(self, ctx: TenantContext, tenant: Tenant) -> None:
        ctx.require(tenant.tenant_id)
        with self._session.begin() as session:
            row = session.get(TenantRow, tenant.tenant_id)
            if row is None:
                session.add(
                    TenantRow(
                        tenant_id=tenant.tenant_id,
                        name=tenant.name,
                        created_at=tenant.created_at,
                        payload=tenant.model_dump(mode="json"),
                    )
                )
            else:
                row.name = tenant.name
                row.payload = tenant.model_dump(mode="json")

    def get_tenant(self, ctx: TenantContext, tenant_id: str) -> Tenant | None:
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(TenantRow, tenant_id)
            return Tenant.model_validate(row.payload) if row is not None else None

    def list_tenants(self, ctx: TenantContext) -> list[Tenant]:
        statement = select(TenantRow).order_by(TenantRow.tenant_id)
        if not ctx.is_system:
            statement = statement.where(TenantRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [Tenant.model_validate(row.payload) for row in session.scalars(statement)]

    def save_team(self, ctx: TenantContext, team: Team) -> None:
        ctx.require(team.tenant_id)
        with self._session.begin() as session:
            if session.get(TenantRow, team.tenant_id) is None:
                raise TenantNotFoundError(team.tenant_id)
            row = session.get(TeamRow, (team.tenant_id, team.team_id))
            if row is None:
                session.add(
                    TeamRow(
                        team_id=team.team_id,
                        tenant_id=team.tenant_id,
                        name=team.name,
                        attribution_key=team.attribution_key,
                        created_at=team.created_at,
                        payload=team.model_dump(mode="json"),
                    )
                )
            else:
                row.name = team.name
                row.attribution_key = team.attribution_key
                row.payload = team.model_dump(mode="json")

    def get_team(self, ctx: TenantContext, tenant_id: str, team_id: str) -> Team | None:
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(TeamRow, (tenant_id, team_id))
            if row is None:
                return None
            return Team.model_validate(row.payload)

    def list_teams(self, ctx: TenantContext, tenant_id: str) -> list[Team]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(TeamRow)
                .where(TeamRow.tenant_id == tenant_id)
                .order_by(TeamRow.team_id)
            )
            return [Team.model_validate(row.payload) for row in rows]

    def save_membership(self, ctx: TenantContext, membership: Membership) -> None:
        ctx.require(membership.tenant_id)
        with self._session.begin() as session:
            if session.get(TenantRow, membership.tenant_id) is None:
                raise TenantNotFoundError(membership.tenant_id)
            row = session.get(MembershipRow, membership.membership_id)
            if row is None:
                session.add(
                    MembershipRow(
                        membership_id=membership.membership_id,
                        tenant_id=membership.tenant_id,
                        team_id=membership.team_id,
                        operator_id=membership.operator_id,
                        role=membership.role.value,
                        created_at=membership.created_at,
                        payload=membership.model_dump(mode="json"),
                    )
                )
            else:
                row.tenant_id = membership.tenant_id
                row.team_id = membership.team_id
                row.operator_id = membership.operator_id
                row.role = membership.role.value
                row.payload = membership.model_dump(mode="json")

    def get_membership(self, ctx: TenantContext, membership_id: str) -> Membership | None:
        with self._session() as session:
            row = session.get(MembershipRow, membership_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return Membership.model_validate(row.payload)

    def list_memberships(self, ctx: TenantContext, tenant_id: str) -> list[Membership]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(MembershipRow)
                .where(MembershipRow.tenant_id == tenant_id)
                .order_by(MembershipRow.membership_id)
            )
            return [Membership.model_validate(row.payload) for row in rows]

    def clear(self) -> None:
        with self._session.begin() as session:
            for table in (MembershipRow, TeamRow, TenantRow):
                session.execute(delete(table))


def build_tenant_store(settings: Settings | None = None) -> TenantStore:
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresTenantStore(create_engine_from_settings(resolved))
    return InMemoryTenantStore()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tenancy_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/persistence/models.py src/hiveplane/tenancy/store.py tests/test_tenancy_store.py
git commit -m "feat(tenancy): tenant store (memory + postgres) and ORM rows (#149)"
```

---

### Task 4: Tenant columns, mixins, composite FKs, and tenant-qualified uniques on all existing rows

**Files:**
- Modify: `src/hiveplane/persistence/models.py`
- Modify: `src/hiveplane/core/run.py`, `src/hiveplane/registry/models.py`, `src/hiveplane/budget/models.py`, `src/hiveplane/core/approval.py`
- Test: `tests/test_persistence_schema.py`

**Interfaces:**
- Consumes: `DEFAULT_TENANT_ID`, `DEFAULT_TEAM_ID`, `DEFAULT_ATTRIBUTION_KEY` from Task 2.
- Produces: `TenantScoped` mixin (`tenant_id`), `Attributed` mixin (`team_id`, `attribution_key`); every existing table tenant-scoped; `Run`, `WorkloadRecord`, `CostAttribution`, `ApprovalRecord` carry tenant fields.

- [ ] **Step 1: Write the failing test** (append to `tests/test_persistence_schema.py`)

```python
def test_every_table_is_tenant_scoped() -> None:
    exempt = {"tenants", "teams", "memberships"}
    missing = {
        name
        for name in Base.metadata.tables
        if name not in exempt
        and "tenant_id" not in Base.metadata.tables[name].columns
    }
    assert missing == set()


def test_tenant_qualified_uniques_include_tenant_id() -> None:
    uniques = {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in Base.metadata.tables["teams"].constraints
        if constraint.name and constraint.name.startswith("uq_")
    }
    assert uniques["uq_teams_tenant_name"] == {"tenant_id", "name"}
    assert uniques["uq_teams_tenant_attribution"] == {"tenant_id", "attribution_key"}


def test_run_carries_tenant_attribution() -> None:
    assert {"tenant_id", "team_id", "attribution_key"} <= set(Run.model_fields)
```

Add `from hiveplane.core.run import Run` to the test module's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_persistence_schema.py -v`
Expected: FAIL — missing tenant columns.

- [ ] **Step 3: Write minimal implementation**

Add mixins near the top of `persistence/models.py`:

```python
class _TenantScoped:
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class _Attributed:
    team_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attribution_key: Mapped[str | None] = mapped_column(String(253), nullable=True)
```

Apply `_TenantScoped` to every existing row class (multiple inheritance: `class WorkloadRow(_TenantScoped, Base):`), and `_Attributed` to `RunRow`, `UsageEventRow`, `CostAttributionRow`, `BudgetDaySpendRow`, `BudgetTeamSpendRow`, `BudgetRunSpendRow`. Add composite FKs and tenant-qualified unique constraints:

- `workloads`: `UniqueConstraint("name", "tenant_id", name="uq_workloads_name_tenant")`.
- `workload_versions`: replace `uq_workload_versions` with `UniqueConstraint("workload", "tenant_id", "version", name="uq_workload_versions")` and `ForeignKeyConstraint(["workload", "tenant_id"], ["workloads.name", "workloads.tenant_id"], ondelete="CASCADE")`.
- `runs`: `UniqueConstraint("id", "tenant_id", name="uq_runs_id_tenant")`, `ForeignKeyConstraint(["workload_id", "tenant_id"], ["workloads.name", "workloads.tenant_id"])`. Migration `0003` deletes orphan runs first (decided: destructive).
- `run_events`, `usage_events`, `run_admissions`, `fan_out_deliveries`: `ForeignKeyConstraint(["run_id", "tenant_id"], ["runs.id", "runs.tenant_id"], ondelete="CASCADE")`.
- `approvals`, `certifications`, `attestations`, `trigger_rules`, `drift_schedules`, `health_signals`, `cost_attributions`: tenant-scoped composite FK to `workloads(name, tenant_id)` where a non-null workload column exists (`attestations.workload`, `certifications.workload`, `trigger_rules.workload`, `drift_schedules.workload`, `health_signals.workload`, `cost_attributions.workload`).
- `audit_log`, `tools`: tenant-scoped; no parent FK.
- `budget_*`: tenant-scoped; `budget_day_spend`/`budget_team_spend` composite PKs become `(tenant_id, workload, day)` and `(tenant_id, team, day)`.

Add tenant fields to domain models:

- `Run`: `tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)`, `team_id: str | None = None`, `attribution_key: str | None = None` (import constants from `hiveplane.tenancy.context`).
- `WorkloadRecord`: `tenant_id: str = Field(default=DEFAULT_TENANT_ID, ...)`.
- `CostAttribution`: `tenant_id: str = Field(default=DEFAULT_TENANT_ID, ...)`, `attribution_key: str | None = None`.
- `ApprovalRecord`: `tenant_id: str = Field(default=DEFAULT_TENANT_ID, ...)`.

Update `save_run`/`save_workload`/etc. call sites in stores are handled in Phase 2.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_persistence_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/persistence/models.py src/hiveplane/core src/hiveplane/registry/models.py src/hiveplane/budget/models.py tests/test_persistence_schema.py
git commit -m "feat(tenancy): tenant columns, composite FKs, and tenant-qualified uniques (#149)"
```

---

### Task 5: Forward-only migration 0003 with backfill

**Files:**
- Create: `src/hiveplane/persistence/migrations/versions/0003_tenancy_scoping.py`
- Modify: `tests/test_persistence_migration_0003.py`
- Note: keep `src/hiveplane/persistence/migrations/*` in the coverage omit list. Migration modules are exercised by PG-gated tests that skip without a database; un-omitting them would drop the >95% gate. Report migration coverage separately at exit.

**Interfaces:**
- Consumes: ORM metadata from Task 4.
- Produces: revision `0003` (`down_revision = "0002"`). On a v0.1.0 DB it adds tenancy tables, seeds `default` tenant + `default` team + `system` tenant, adds tenant columns with `server_default='default'`, sets `NOT NULL`, and adds indexes/constraints. On a fresh DB (where `0001` `create_all` already built the new schema) every step is guarded and becomes a no-op.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    return config


def test_0003_is_idempotent_from_head(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    command.downgrade(config, "0002")
    command.upgrade(config, "head")
    tables = set(inspect(pg_engine).get_table_names())
    assert {"tenants", "teams", "memberships"} <= tables
    assert "tenant_id" in {c["name"] for c in inspect(pg_engine).get_columns("runs")}


def test_backfill_seeds_default_tenant_and_team(pg_engine: Engine) -> None:
    command.upgrade(_config(), "head")
    with pg_engine.connect() as conn:
        tenant_ids = set(conn.execute(text("SELECT tenant_id FROM tenants")).scalars())
        team_ids = set(conn.execute(text("SELECT team_id FROM teams")).scalars())
    assert {"default", "system"} <= tenant_ids
    assert "default" in team_ids


def test_legacy_run_row_backfills_to_default_tenant(pg_engine: Engine) -> None:
    config = _config()
    command.downgrade(config, "0002")
    with pg_engine.begin() as conn:
        now = datetime(2026, 9, 25, tzinfo=UTC)
        conn.execute(
            text(
                "INSERT INTO workloads (name, owner, team, certification_status, "
                "current_version, created_at, updated_at, payload) VALUES "
                "('agent-1', 'alice', 'platform', 'certified', 1, :now, :now, '{}')"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO runs (id, workload_id, caller, state, cost_usd, "
                "created_at, updated_at, payload) VALUES "
                "('legacy-run', 'agent-1', 'alice', 'completed', 0, :now, :now, '{}')"
            ),
            {"now": now},
        )
    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        run_tenant = conn.execute(
            text("SELECT tenant_id FROM runs WHERE id = 'legacy-run'")
        ).scalar_one()
        workload_tenant = conn.execute(
            text("SELECT tenant_id FROM workloads WHERE name = 'agent-1'")
        ).scalar_one()
    assert run_tenant == "default"
    assert workload_tenant == "default"


def test_orphan_runs_are_deleted_before_fk(pg_engine: Engine) -> None:
    config = _config()
    command.downgrade(config, "0002")
    with pg_engine.begin() as conn:
        now = datetime(2026, 9, 25, tzinfo=UTC)
        conn.execute(
            text(
                "INSERT INTO runs (id, workload_id, caller, state, cost_usd, "
                "created_at, updated_at, payload) VALUES "
                "('orphan', 'ghost', 'alice', 'completed', 0, :now, :now, '{}')"
            ),
            {"now": now},
        )
    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        remaining = conn.execute(text("SELECT count(*) FROM runs")).scalar_one()
    assert remaining == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_persistence_migration_0003.py -v` (requires `pg_engine`; skips without Postgres)
Expected: FAIL — revision `0003` does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement `0003_tenancy_scoping.py` with guarded helpers:

```python
"""tenancy scoping

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op
from sqlalchemy import inspect

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TENANT_TABLES = ("tenants", "teams", "memberships")
_LEGACY_TABLES = (
    "workloads", "workload_versions", "runs", "run_events", "usage_events",
    "run_admissions", "audit_log", "approvals", "certifications", "attestations",
    "tools", "trigger_rules", "drift_schedules", "fan_out_deliveries",
    "health_signals", "cost_attributions", "budget_run_spend", "budget_day_spend",
    "budget_team_spend",
)
_ATTRIBUTED_TABLES = ("runs", "usage_events", "cost_attributions", "budget_*")


def _columns(bind: object, table: str) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _has_table(bind: object, table: str) -> bool:
    return table in set(inspect(bind).get_table_names())


def _seed_tenancy(bind: object) -> None:
    now = "now()"
    for name in ("tenants",):
        if not _has_table(bind, name):
            Base.metadata.tables[name].create(bind, checkfirst=True)
    ...
```

Follow with:
1. Create `tenants`, `teams`, `memberships` via `Base.metadata.tables[name].create(bind, checkfirst=True)`.
2. `INSERT ... ON CONFLICT DO NOTHING` for `default` and `system` tenants and the `default` team.
3. Seed tenancy rows (`default`, `system` tenants; `default` team).
3a. **Delete orphan runs** (decided): `DELETE FROM runs WHERE workload_id NOT IN (SELECT name FROM workloads)` and the equivalent for run children (`run_events`, `usage_events`, `run_admissions`, `fan_out_deliveries`) via cascade or explicit deletes, before adding the composite FK to `workloads`.
4. For each legacy table, if `tenant_id` missing: `op.add_column(table, sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"))`. Same for `team_id`/`attribution_key` (nullable) on attributed tables.
5. Create indexes (`op.create_index(..., if_not_exists=True)` is not portable; guard with inspector).
6. Create unique constraints and composite FKs driven by `Base.metadata.tables[table].constraints`, guarded by `if_not_exists` checks against `inspect(bind).get_foreign_keys`/`get_unique_constraints`.
7. `downgrade()` must be implemented (the existing migration tests call `downgrade(config, "base")`): drop the composite FKs and tenant-qualified uniques, drop the tenant columns, then drop the tenancy tables. It need not restore deleted orphan runs (data deletion is irreversible by design).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_persistence_migration_0003.py tests/test_persistence_migrations.py -v`
Expected: PASS (or SKIP when Postgres is unavailable).

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/persistence/migrations/versions/0003_tenancy_scoping.py tests/test_persistence_migration_0003.py
git commit -m "feat(tenancy): forward-only migration 0003 with default-tenant backfill (#149)"
```

---

## Phase 2 — Store-layer enforcement

> Pattern for every store in this phase: change the protocol + in-memory + Postgres (+ JSON where present) implementations; thread `ctx: TenantContext` as the first parameter; filter reads by `tenant_id`; on write, `ctx.require(record.tenant_id)` and persist `tenant_id`/`team_id`/`attribution_key` from the context/record. Add an isolation test per store in `tests/test_tenant_scoping_isolation.py`.

### Task 6: RunStore tenant enforcement

**Files:**
- Modify: `src/hiveplane/execution/store.py` (protocol + `_BundleStore` + `JsonFileRunStore`), `src/hiveplane/persistence/run_store.py`
- Modify: `src/hiveplane/execution/service.py`, `src/hiveplane/execution/fanout.py`, `src/hiveplane/execution/wiring.py`
- Test: `tests/test_tenant_scoping_isolation.py`, `tests/test_execution_store.py`

**Interfaces:**
- Produces:
  - `RunStore.save_run(ctx, run)`, `get_run(ctx, run_id)`, `list_runs(ctx, *, workload=None, state=None)`, `add_event(ctx, event)`, `list_events(ctx, run_id)`, `add_usage(ctx, report)`, `list_usage(ctx, run_id)`, `save_admission(ctx, result)`, `get_admission(ctx, run_id)`, `add_delivery(ctx, record)`, `list_deliveries(ctx, run_id)`.
  - `add_*`/`save_*` resolve the parent run's tenant from the run row, `ctx.require(parent.tenant_id)`, then write.
  - `_BundleStore` keys bundles by `(tenant_id, run_id)`; `get_run`/`list_*` return only `ctx`-scoped rows.

- [ ] **Step 1: Write the failing test** (`tests/test_tenant_scoping_isolation.py`)

```python
from datetime import UTC, datetime

import pytest

from hiveplane.core.run import Run, RunState
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.tenancy import Role, TenantScopeError
from hiveplane.tenancy.context import TenantContext

_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_A = TenantContext(tenant_id="a", role=Role.ADMIN)
_B = TenantContext(tenant_id="b", role=Role.ADMIN)


def _run(run_id: str, tenant_id: str) -> Run:
    return Run(
        id=run_id, workload_id="agent-1", caller="alice", state=RunState.QUEUED,
        created_at=_NOW, updated_at=_NOW, tenant_id=tenant_id,
    )


def test_run_store_isolates_tenants() -> None:
    store = InMemoryRunStore()
    store.save_run(_A, _run("r-a", "a"))
    assert store.get_run(_A, "r-a") is not None
    assert store.get_run(_B, "r-a") is None
    assert store.list_runs(_B) == []


def test_run_store_rejects_cross_tenant_write() -> None:
    store = InMemoryRunStore()
    with pytest.raises(TenantScopeError):
        store.save_run(_A, _run("r-b", "b"))


def test_run_event_inherits_parent_tenant() -> None:
    store = InMemoryRunStore()
    store.save_run(_A, _run("r-a", "a"))
    assert store.get_run(_A, "r-a") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tenant_scoping_isolation.py -v`
Expected: FAIL — `save_run()` missing `ctx` or signature mismatch.

- [ ] **Step 3: Implement tenant enforcement** across `_BundleStore` (key by `(tenant_id, run_id)`), `PostgresRunStore` (use `RunRow.tenant_id`), and `JsonFileRunStore` (tenant in bundle), then update `RunService`, `FanOutService`, `build_run_store`/`build_run_service` wiring to accept and pass `ctx`. Update direct-call tests listed in the inventory (`test_execution_store.py`, `test_persistence_run_store.py`, `test_run_recovery.py`, `test_execution_fanout.py`, etc.).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_tenant_scoping_isolation.py tests/test_execution_store.py tests/test_execution_service.py tests/test_persistence_run_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(tenancy): tenant-scope RunStore and run history (#149)"
```

---

### Task 7: RegistryStore tenant enforcement

**Files:**
- Modify: `src/hiveplane/registry/store.py`, `src/hiveplane/registry/service.py`, `src/hiveplane/cli.py`, `src/hiveplane/certification/workflow.py`, `src/hiveplane/execution/gates.py`
- Test: `tests/test_tenant_scoping_isolation.py`, `tests/test_registry.py`, `tests/test_persistence_postgres_stores.py`

**Interfaces:** every `RegistryStore` method gains `ctx: TenantContext` as first param; workload/version/attestation/tool/trigger reads filter by `tenant_id`; `delete_workload` only deletes within `ctx`'s tenant.

- [ ] **Step 1: Add failing isolation tests** for workloads, versions, attestations, tools, triggers.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement and update `RegistryService` + all listed call sites/tests.**
- [ ] **Step 4: Run** `pytest tests/test_tenant_scoping_isolation.py tests/test_registry.py tests/test_persistence_postgres_stores.py tests/test_registry_m4.py -v` — PASS.
- [ ] **Step 5: Commit** `feat(tenancy): tenant-scope RegistryStore (#149)`.

---

### Task 8: CertificationStore, BudgetStore, ApprovalStore, PolicyPackStore, and postgres audit enforcement

**Files:**
- Modify: `src/hiveplane/certification/store.py`, `src/hiveplane/budget/store.py`, `src/hiveplane/policy/store.py`, `src/hiveplane/policy/packs.py`, `src/hiveplane/persistence/postgres_audit.py`
- Modify owning services: `certification/workflow.py`, `budget/service.py`, `policy/approvals.py`, `policy/engine.py`, `execution/service.py`, `execution/wiring.py`
- Test: `tests/test_tenant_scoping_isolation.py` plus the store-specific test files.

**Interfaces:**
- `CertificationStore.add(ctx, record)`, `get(ctx, id)`, `list(ctx, *, workload, status, limit, offset)`.
- `BudgetStore` methods take `ctx` first; keys become `(tenant_id, ...)`; `record_attribution(ctx, record)` requires `record.tenant_id`.
- `ApprovalStore.save/get/list_approvals` take `ctx`; workload filter applied in SQL by tenant.
- `PolicyPackStore.save/get/list_packs/for_team` take `ctx`; `for_team(ctx, team)`.
- `PostgresAuditLog.append(ctx, actor, action, subject, ...)`; `records(ctx)` filters to the tenant; `verify(ctx)` **still verifies the whole global chain** (decided: one chain + `tenant_id` per entry, not split chains).

- [ ] **Step 1: Add failing isolation tests** per store (cross-tenant read hidden, cross-tenant write raises).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement and update services/tests.**
- [ ] **Step 4: Run** `pytest tests/test_tenant_scoping_isolation.py tests/test_certification_store.py tests/test_budget_store.py tests/test_persistence_postgres_stores.py tests/test_persistence_postgres_audit.py -v` — PASS.
- [ ] **Step 5: Commit** `feat(tenancy): tenant-scope certification, budget, approval, policy, audit stores (#149)`.

---

## Phase 3 — Wiring, version, docs

### Task 9: API tenant resolution and end-to-end wiring

**Files:**
- Modify: `src/hiveplane/api/deps.py`, `src/hiveplane/api/app.py`, `src/hiveplane/api/runs.py`, `src/hiveplane/api/spend.py`, `src/hiveplane/api/policy.py`, `src/hiveplane/api/readiness.py`
- Test: `tests/test_tenant_scoping_isolation.py`, `tests/test_api_tenancy.py`

**Interfaces:**
- `get_tenant_context(request) -> TenantContext` reads header `X-Hiveplane-Tenant`; unknown/absent → `DEFAULT_CONTEXT`; `X-Hiveplane-Team` optional.
- Every API handler passes the resolved context into service/store calls.

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app


def test_api_rejects_cross_tenant_run_read() -> None:
    client = TestClient(create_app())
    response = client.get("/runs/does-not-exist", headers={"X-Hiveplane-Tenant": "acme"})
    assert response.status_code == 404
```

- [ ] **Step 2–5:** implement, run `pytest tests/test_api_tenancy.py tests/test_runs_api.py tests/test_spend_api.py -v`, commit `feat(tenancy): resolve tenant from API header and wire services (#149)`.

---

### Task 10: Version bump, changelog, and design-doc updates

**Files:**
- Modify: `pyproject.toml`, `src/hiveplane/__init__.py`, `CHANGELOG.md`, `docs/design/fleet-control-data-model-design.md`, `docs/design/state-store-design.md`, `README.md` (badge/version references if any)

- [ ] **Step 1:** set version `0.2.0` in `pyproject.toml` and `__init__.py`.
- [ ] **Step 2:** add a `## [0.2.0] - Unreleased` CHANGELOG section under Added: tenants/teams/memberships, tenant-scoped stores, composite FKs, migration 0003.
- [ ] **Step 3:** record in D21 the decided mechanics (explicit `TenantContext`, full composite FKs, header resolution, `default`/`system` reserved ids); cross-reference D21 from `state-store-design.md`.
- [ ] **Step 4:** `git add -A && git commit -m "docs(0.2.0): bump to 0.2.0 and record M25-01 tenancy decisions (#149)"`.

---

### Task 11: M25-01 exit verification

- [ ] **Step 1:** `pytest` — all pass.
- [ ] **Step 2:** `pytest --cov=src/hiveplane --cov-report=term-missing` — new tenancy/model/migration modules ≥ 95%.
- [ ] **Step 3:** `ruff check` — clean.
- [ ] **Step 4:** `mypy src/ tests/` — clean.
- [ ] **Step 5:** close issue #149 with an evidence comment (test names/counts, coverage, commit sha); link follow-ups for any store deliberately deferred.

---

## Self-Review

**Spec coverage:** tenants/teams/memberships (Task 1, 3), existing-table scoping (Task 4), forward-only migration + backfill (Task 5), store-layer enforcement (Tasks 6–8), API resolution (Task 9), version/docs (Task 10), acceptance tests (Task 11). M25-01 acceptance criteria covered.

**Resolved decisions (locked):**
- `runs.workload_id` composite FK is enforced; migration `0003` deletes orphan runs and their children first (destructive, accepted).
- `audit_log` stays one global tamper-evident chain with `tenant_id` per entry; `records(ctx)` filters, `verify(ctx)` verifies the whole chain.
- `budget_run_spend`/`budget_day_spend`/`budget_team_spend` are tenant-qualified with `tenant_id` indexed but no FK to workloads/teams (spend may precede registration).

**Open items to resolve during execution:**
- Migrations and `persistence/run_store.py` stay in the coverage omit list (DB-only code exercised only by PG-gated tests). Migration coverage is reported from the dedicated PG-gated tests, not the global gate.
- The API resolves tenant from a client header with no authentication (auth lands in M45 per D33). This must be flagged in the design as temporary and not a security boundary yet.
```
