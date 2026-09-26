"""Secret and secret-reference models (M25-05, D21/D33).

A secret stores ciphertext at rest with a key-id reference and rotation
metadata; plaintext never persists. A secret reference records where a secret
is injected (run, environment variable, or file path) so access is auditable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class SecretScope(StrEnum):
    """The boundary at which a secret is visible."""

    TENANT = "tenant"
    TEAM = "team"
    WORKLOAD = "workload"


class InjectionTarget(StrEnum):
    """How a secret is injected into a run."""

    ENV = "env"
    FILE = "file"


class Secret(BaseModel):
    """A ciphertext-at-rest secret with rotation metadata."""

    model_config = ConfigDict(extra="forbid")

    secret_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    scope: SecretScope
    scope_ref: str | None = Field(default=None, max_length=253)
    ciphertext: str = Field(min_length=1)
    key_id: str = Field(min_length=1, max_length=253)
    created_at: AwareDatetime
    rotated_at: AwareDatetime | None = None
    rotation_interval_days: int | None = Field(default=None, gt=0)
    next_rotation_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _scoped_secrets_need_a_ref(self) -> Self:
        if self.scope is not SecretScope.TENANT and not self.scope_ref:
            raise ValueError(f"{self.scope.value}-scoped secrets require 'scope_ref'")
        if self.scope is SecretScope.TENANT and self.scope_ref is not None:
            raise ValueError("tenant-scoped secrets must not set 'scope_ref'")
        return self


class SecretReference(BaseModel):
    """A resolvable reference to a secret by name and scope."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=253)
    scope: SecretScope
    scope_ref: str | None = Field(default=None, max_length=253)


class SecretRef(BaseModel):
    """A record of a secret injected into a run."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    secret_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    injection_target: InjectionTarget
    target_name: str = Field(min_length=1, max_length=253)
