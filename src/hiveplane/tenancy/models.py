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
