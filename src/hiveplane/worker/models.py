"""Worker daemon models: identity tokens, registration, leases, reports (M46).

Re-exports the M25 worker models (D21) and adds the M46 execution records:
signed worker tokens, registration/heartbeat requests, run assignments, and
fenced worker reports used for lease-based execution.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.fleet.workers import (
    Worker,
    WorkerCapabilities,
    WorkerHeartbeat,
    WorkerLease,
    WorkerState,
)

__all__ = [
    "RunAssignment",
    "StaleLeaseError",
    "UnknownWorkerError",
    "Worker",
    "WorkerCapabilities",
    "WorkerFleetEntry",
    "WorkerHeartbeat",
    "WorkerHeartbeatRequest",
    "WorkerIdentityError",
    "WorkerLease",
    "WorkerNotReadyError",
    "WorkerRegistrationRequest",
    "WorkerReport",
    "WorkerState",
    "WorkerToken",
    "WorkerTokenIssued",
]


class WorkerError(Exception):
    """Base class for worker registry failures."""


class WorkerIdentityError(WorkerError):
    """Raised when a worker token is missing, invalid, expired, or revoked."""


class UnknownWorkerError(WorkerError):
    """Raised when a worker id is not registered."""

    def __init__(self, worker_id: str) -> None:
        super().__init__(f"unknown worker {worker_id!r}")
        self.worker_id = worker_id


class WorkerNotReadyError(WorkerError):
    """Raised when a worker in a non-ready state is asked to take work."""

    def __init__(self, worker_id: str, state: WorkerState) -> None:
        super().__init__(f"worker {worker_id!r} is {state.value}")
        self.worker_id = worker_id
        self.state = state


class StaleLeaseError(WorkerError):
    """Raised when a report/accept carries a superseded fence (zombie worker)."""

    def __init__(self, lease_id: str, fencing_token: int) -> None:
        super().__init__(f"lease {lease_id!r} fence {fencing_token} is stale")
        self.lease_id = lease_id
        self.fencing_token = fencing_token


class WorkerToken(BaseModel):
    """A signed worker identity token record (M46-05)."""

    model_config = ConfigDict(extra="forbid")

    token_id: str = Field(min_length=1, max_length=128)
    worker_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    key_id: str = Field(min_length=1)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None


class WorkerTokenIssued(BaseModel):
    """A newly issued worker token; the plaintext is shown exactly once."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(repr=False)
    record: WorkerToken


class WorkerRegistrationRequest(BaseModel):
    """A worker's registration with its identity token and capabilities."""

    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=64)
    token: str = Field(repr=False)
    version: str = Field(min_length=1, max_length=64)
    capabilities: WorkerCapabilities = Field(default_factory=WorkerCapabilities)


class WorkerHeartbeatRequest(BaseModel):
    """A worker heartbeat: identity, load, and the leases being renewed."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(repr=False)
    running: int = Field(default=0, ge=0)
    max_concurrency: int = Field(default=1, ge=1)
    lease_ids: list[str] = Field(default_factory=list)


class RunAssignment(BaseModel):
    """A run leased to a worker for execution."""

    model_config = ConfigDict(extra="forbid")

    lease: WorkerLease
    workload_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    reassigned_from: str | None = None


class WorkerReport(BaseModel):
    """A worker's state/usage report for a leased run (carries the fence)."""

    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=64)
    lease_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    fencing_token: int = Field(ge=0)
    state: WorkerState
    usage_usd: float = Field(default=0.0, ge=0.0)


class WorkerFleetEntry(BaseModel):
    """A fleet-view row: worker liveness, load, and active leases (M46-07)."""

    model_config = ConfigDict(extra="forbid")

    worker: Worker
    load: int = Field(ge=0)
    active_leases: list[str] = Field(default_factory=list)
