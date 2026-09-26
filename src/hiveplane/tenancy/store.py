"""Tenant/team/membership storage (#149)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import MembershipRow, TeamRow, TenantRow
from hiveplane.tenancy.context import TenantContext
from hiveplane.tenancy.errors import TenantNotFoundError
from hiveplane.tenancy.models import Membership, Team, Tenant


class TenantStore(Protocol):
    """Tenant-scoped storage for tenants, teams, and memberships."""

    def save_tenant(self, ctx: TenantContext, tenant: Tenant) -> None: ...

    def get_tenant(self, ctx: TenantContext, tenant_id: str) -> Tenant | None: ...

    def list_tenants(self, ctx: TenantContext) -> list[Tenant]: ...

    def save_team(self, ctx: TenantContext, team: Team) -> None: ...

    def get_team(self, ctx: TenantContext, tenant_id: str, team_id: str) -> Team | None: ...

    def list_teams(self, ctx: TenantContext, tenant_id: str) -> list[Team]: ...

    def save_membership(self, ctx: TenantContext, membership: Membership) -> None: ...

    def get_membership(self, ctx: TenantContext, membership_id: str) -> Membership | None: ...

    def list_memberships(self, ctx: TenantContext, tenant_id: str) -> list[Membership]: ...


class InMemoryTenantStore:
    """In-memory tenant store with explicit tenant scoping."""

    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._teams: dict[tuple[str, str], Team] = {}
        self._memberships: dict[str, Membership] = {}

    def save_tenant(self, ctx: TenantContext, tenant: Tenant) -> None:
        """Store a tenant keyed by its id."""
        ctx.require(tenant.tenant_id)
        self._tenants[tenant.tenant_id] = tenant.model_copy(deep=True)

    def get_tenant(self, ctx: TenantContext, tenant_id: str) -> Tenant | None:
        """Return a tenant by id when in scope, else ``None``."""
        if not ctx.scopes(tenant_id):
            return None
        tenant = self._tenants.get(tenant_id)
        return tenant.model_copy(deep=True) if tenant is not None else None

    def list_tenants(self, ctx: TenantContext) -> list[Tenant]:
        """Return visible tenants, sorted by id."""
        if ctx.is_system:
            tenants = list(self._tenants.values())
        else:
            tenants = [tenant for tenant in self._tenants.values() if ctx.scopes(tenant.tenant_id)]
        tenants.sort(key=lambda tenant: tenant.tenant_id)
        return [tenant.model_copy(deep=True) for tenant in tenants]

    def save_team(self, ctx: TenantContext, team: Team) -> None:
        """Store a team; requires an in-scope, existing tenant."""
        ctx.require(team.tenant_id)
        if team.tenant_id not in self._tenants:
            raise TenantNotFoundError(team.tenant_id)
        self._teams[(team.tenant_id, team.team_id)] = team.model_copy(deep=True)

    def get_team(self, ctx: TenantContext, tenant_id: str, team_id: str) -> Team | None:
        """Return a team when its tenant is in scope, else ``None``."""
        if not ctx.scopes(tenant_id):
            return None
        team = self._teams.get((tenant_id, team_id))
        return team.model_copy(deep=True) if team is not None else None

    def list_teams(self, ctx: TenantContext, tenant_id: str) -> list[Team]:
        """Return visible teams for a tenant, sorted by id."""
        if not ctx.scopes(tenant_id):
            return []
        teams = [t for (tid, _), t in self._teams.items() if tid == tenant_id]
        teams.sort(key=lambda team: team.team_id)
        return [team.model_copy(deep=True) for team in teams]

    def save_membership(self, ctx: TenantContext, membership: Membership) -> None:
        """Store a membership; requires an in-scope, existing tenant."""
        ctx.require(membership.tenant_id)
        if membership.tenant_id not in self._tenants:
            raise TenantNotFoundError(membership.tenant_id)
        self._memberships[membership.membership_id] = membership.model_copy(deep=True)

    def get_membership(self, ctx: TenantContext, membership_id: str) -> Membership | None:
        """Return a membership when its tenant is in scope, else ``None``."""
        membership = self._memberships.get(membership_id)
        if membership is None or not ctx.scopes(membership.tenant_id):
            return None
        return membership.model_copy(deep=True)

    def list_memberships(self, ctx: TenantContext, tenant_id: str) -> list[Membership]:
        """Return visible memberships for a tenant, sorted by id."""
        if not ctx.scopes(tenant_id):
            return []
        memberships = [m for m in self._memberships.values() if m.tenant_id == tenant_id]
        memberships.sort(key=lambda m: m.membership_id)
        return [m.model_copy(deep=True) for m in memberships]


class PostgresTenantStore:
    """A durable tenant store backed by PostgreSQL (#149)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_tenant(self, ctx: TenantContext, tenant: Tenant) -> None:
        """Insert or update a tenant."""
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
        """Return a tenant by id when in scope, else ``None``."""
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(TenantRow, tenant_id)
            return Tenant.model_validate(row.payload) if row is not None else None

    def list_tenants(self, ctx: TenantContext) -> list[Tenant]:
        """Return visible tenants, sorted by id."""
        statement = select(TenantRow).order_by(TenantRow.tenant_id)
        if not ctx.is_system:
            statement = statement.where(TenantRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [Tenant.model_validate(row.payload) for row in session.scalars(statement)]

    def save_team(self, ctx: TenantContext, team: Team) -> None:
        """Insert or update a team; requires an in-scope, existing tenant."""
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
        """Return a team when its tenant is in scope, else ``None``."""
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(TeamRow, (tenant_id, team_id))
            if row is None:
                return None
            return Team.model_validate(row.payload)

    def list_teams(self, ctx: TenantContext, tenant_id: str) -> list[Team]:
        """Return visible teams for a tenant, sorted by id."""
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(TeamRow).where(TeamRow.tenant_id == tenant_id).order_by(TeamRow.team_id)
            )
            return [Team.model_validate(row.payload) for row in rows]

    def save_membership(self, ctx: TenantContext, membership: Membership) -> None:
        """Insert or update a membership; requires an in-scope, existing tenant."""
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
        """Return a membership when its tenant is in scope, else ``None``."""
        with self._session() as session:
            row = session.get(MembershipRow, membership_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return Membership.model_validate(row.payload)

    def list_memberships(self, ctx: TenantContext, tenant_id: str) -> list[Membership]:
        """Return visible memberships for a tenant, sorted by id."""
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
        """Delete all tenancy rows; used by tests and destructive operations."""
        with self._session.begin() as session:
            for table in (MembershipRow, TeamRow, TenantRow):
                session.execute(delete(table))


def build_tenant_store(settings: Settings | None = None) -> TenantStore:
    """Build the configured tenant store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresTenantStore(create_engine_from_settings(resolved))
    return InMemoryTenantStore()
