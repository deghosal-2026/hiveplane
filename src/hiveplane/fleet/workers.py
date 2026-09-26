"""Distributed worker models (M25-06, D21/D34).

A worker advertises identity, capabilities, and capacity; its status and
last-seen drive scheduling. Leases hold the current run assignment with a
fencing token, and heartbeats are a bounded rolling liveness signal. Lease
expiry drives reassignment.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from hiveplane.core.spec import RuntimeAdapter
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class WorkerState(StrEnum):
    """Lifecycle state of a worker."""

    REGISTERING = "registering"
    READY = "ready"
    BUSY = "busy"
    DRAINING = "draining"
    MAINTENANCE = "maintenance"
    UNHEALTHY = "unhealthy"
    DEREGISTERED = "deregistered"


class WorkerCapabilities(BaseModel):
    """What a worker can execute."""

    model_config = ConfigDict(extra="forbid")

    adapters: list[RuntimeAdapter] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    capacity: int = Field(default=1, ge=0)


class Worker(BaseModel):
    """A registered execution worker."""

    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    state: WorkerState
    capabilities: WorkerCapabilities = Field(default_factory=WorkerCapabilities)
    version: str = Field(min_length=1, max_length=64)
    registered_at: AwareDatetime
    last_seen_at: AwareDatetime


class WorkerLease(BaseModel):
    """A worker's current assignment of a run."""

    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    worker_id: str = Field(min_length=1, max_length=64)
    attempt: int = Field(ge=1)
    granted_at: AwareDatetime
    expires_at: AwareDatetime
    fencing_token: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _expiry_after_grant(self) -> Self:
        if self.expires_at <= self.granted_at:
            raise ValueError("lease 'expires_at' must be after 'granted_at'")
        return self


class WorkerHeartbeat(BaseModel):
    """One liveness signal from a worker."""

    model_config = ConfigDict(extra="forbid")

    heartbeat_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    worker_id: str = Field(min_length=1, max_length=64)
    seen_at: AwareDatetime
    status: WorkerState
