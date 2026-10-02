"""Worker daemon, registration, leases, identity, and crash reclaim (M46)."""

from __future__ import annotations

from hiveplane.worker.daemon import RunRunner, WorkerDaemon
from hiveplane.worker.identity import WorkerIdentityService, new_token_id
from hiveplane.worker.models import (
    RunAssignment,
    StaleLeaseError,
    UnknownWorkerError,
    Worker,
    WorkerCapabilities,
    WorkerError,
    WorkerFleetEntry,
    WorkerHeartbeat,
    WorkerHeartbeatRequest,
    WorkerIdentityError,
    WorkerLease,
    WorkerNotReadyError,
    WorkerRegistrationRequest,
    WorkerReport,
    WorkerState,
    WorkerToken,
    WorkerTokenIssued,
)
from hiveplane.worker.registry import WorkerRegistry, new_heartbeat_id, new_lease_id
from hiveplane.worker.store import (
    InMemoryWorkerStore,
    PostgresWorkerStore,
    WorkerStore,
    build_worker_store,
)

__all__ = [
    "InMemoryWorkerStore",
    "PostgresWorkerStore",
    "RunAssignment",
    "RunRunner",
    "StaleLeaseError",
    "UnknownWorkerError",
    "Worker",
    "WorkerCapabilities",
    "WorkerDaemon",
    "WorkerError",
    "WorkerFleetEntry",
    "WorkerHeartbeat",
    "WorkerHeartbeatRequest",
    "WorkerIdentityError",
    "WorkerIdentityService",
    "WorkerLease",
    "WorkerNotReadyError",
    "WorkerRegistrationRequest",
    "WorkerRegistry",
    "WorkerReport",
    "WorkerState",
    "WorkerStore",
    "WorkerToken",
    "WorkerTokenIssued",
    "build_worker_store",
    "new_heartbeat_id",
    "new_lease_id",
    "new_token_id",
]
