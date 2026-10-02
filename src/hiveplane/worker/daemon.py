"""Worker daemon: pull leased runs, execute them, report under the fence (M46-01)."""

from __future__ import annotations

from typing import Protocol

from hiveplane.fleet.workers import WorkerState
from hiveplane.worker.models import (
    RunAssignment,
    WorkerCapabilities,
    WorkerHeartbeatRequest,
    WorkerRegistrationRequest,
    WorkerReport,
)
from hiveplane.worker.registry import WorkerRegistry


class RunRunner(Protocol):
    """Executes one leased run and reports success."""

    def run(self, assignment: RunAssignment) -> bool: ...


class WorkerDaemon:
    """A stateless worker that pulls and executes runs under leases (D34).

    All durable state lives in the plane; the daemon authenticates, executes
    assigned runs through the adapter path, and reports under its lease fence.
    """

    def __init__(
        self,
        registry: WorkerRegistry,
        *,
        worker_id: str,
        token: str,
        runner: RunRunner,
        tenant_id: str = "default",
        version: str = "0.2.0",
    ) -> None:
        self._registry = registry
        self._worker_id = worker_id
        self._token = token
        self._runner = runner
        self._tenant_id = tenant_id
        self._version = version

    @property
    def worker_id(self) -> str:
        """Return the worker's id."""
        return self._worker_id

    def enroll_and_register(self, capabilities: WorkerCapabilities | None = None) -> None:
        """Enroll (issue a token) and register with the plane."""
        issued = self._registry.enroll(self._worker_id, tenant_id=self._tenant_id)
        self._token = issued.token
        self._registry.register(
            WorkerRegistrationRequest(
                worker_id=self._worker_id,
                token=self._token,
                version=self._version,
                capabilities=capabilities or WorkerCapabilities(),
            ),
            tenant_id=self._tenant_id,
        )

    def heartbeat(self, *, running: int, max_concurrency: int) -> None:
        """Send a heartbeat renewing this worker's active leases."""
        leases = self._registry.leases(self._worker_id, tenant_id=self._tenant_id)
        self._registry.heartbeat(
            self._worker_id,
            WorkerHeartbeatRequest(
                token=self._token,
                running=running,
                max_concurrency=max_concurrency,
                lease_ids=[lease.lease_id for lease in leases],
            ),
            tenant_id=self._tenant_id,
        )

    def tick(self) -> list[WorkerReport]:
        """Execute every currently leased run and report under the fence."""
        reports: list[WorkerReport] = []
        leases = self._registry.leases(self._worker_id, tenant_id=self._tenant_id)
        for lease in leases:
            assignment = RunAssignment(
                lease=lease,
                workload_id=lease.workload_id or lease.run_id,
                run_id=lease.run_id,
            )
            try:
                succeeded = self._runner.run(assignment)
            except Exception:
                succeeded = False
            report = WorkerReport(
                worker_id=self._worker_id,
                lease_id=lease.lease_id,
                run_id=lease.run_id,
                fencing_token=lease.fencing_token,
                state=WorkerState.READY if succeeded else WorkerState.UNHEALTHY,
            )
            self._registry.report(report, token=self._token)
            reports.append(report)
        return reports
