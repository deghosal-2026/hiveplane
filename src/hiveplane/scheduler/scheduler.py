"""Priority+fairness scheduler with limits, backpressure, QoS, preemption (M47).

Runs enter a priority queue ordered by QoS class, explicit priority, and aging
(so strict priority cannot starve lower classes). Per-workload and per-tenant
concurrency limits plus a max queue depth provide backpressure with an explicit
reason. Guaranteed work may preempt best-effort/burstable victims, but only at
idempotent checkpoints, with attribution. Maintenance windows block admission.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.scheduler.models import (
    AdmissionAction,
    AdmissionDecision,
    BackpressureReason,
    PreemptionRecord,
    QosClass,
    QueueEntry,
    QueueSnapshot,
    WaitingReason,
)
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext


class SchedulerConfig(BaseModel):
    """Capacity and fairness knobs for the scheduler."""

    model_config = ConfigDict(extra="forbid")

    per_workload_limit: int = Field(default=8, ge=1)
    per_tenant_limit: int = Field(default=32, ge=1)
    max_queue_depth: int = Field(default=1000, ge=1)
    aging_seconds: float = Field(default=60.0, gt=0.0)


class _Running(BaseModel):
    entry: QueueEntry
    at_checkpoint: bool = False
    started_at: datetime


class Scheduler:
    """The fleet scheduler: admission, ordering, preemption, and visibility."""

    def __init__(
        self,
        config: SchedulerConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config or SchedulerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._queue: dict[str, QueueEntry] = {}
        self._running: dict[str, _Running] = {}
        self._preemptions: list[PreemptionRecord] = []
        self._frozen_all = False
        self._frozen_tenants: set[str] = set()
        self._frozen_workloads: set[str] = set()

    # ------------------------------------------------------------------ #
    # Admission & backpressure
    # ------------------------------------------------------------------ #
    def submit(self, entry: QueueEntry) -> AdmissionDecision:
        """Queue a task or reject it with an explicit backpressure reason."""
        if self._is_frozen(entry.workload, entry.tenant_id):
            return AdmissionDecision(
                entry=entry, action=AdmissionAction.REJECTED, reason=BackpressureReason.MAINTENANCE
            )
        if self._running_for_workload(entry.workload) >= self._config.per_workload_limit:
            return AdmissionDecision(
                entry=entry,
                action=AdmissionAction.REJECTED,
                reason=BackpressureReason.PER_WORKLOAD_LIMIT,
            )
        if self._running_for_tenant(entry.tenant_id) >= self._config.per_tenant_limit:
            return AdmissionDecision(
                entry=entry,
                action=AdmissionAction.REJECTED,
                reason=BackpressureReason.PER_TENANT_LIMIT,
            )
        if len(self._queue) >= self._config.max_queue_depth:
            return AdmissionDecision(
                entry=entry, action=AdmissionAction.REJECTED, reason=BackpressureReason.QUEUE_DEPTH
            )
        self._queue[entry.task_id] = entry
        return AdmissionDecision(entry=entry, action=AdmissionAction.QUEUED)

    # ------------------------------------------------------------------ #
    # Ordering & fairness
    # ------------------------------------------------------------------ #
    def dequeue(self) -> QueueEntry | None:
        """Return the next task by QoS, priority, and aging, and mark it running."""
        if not self._queue:
            return None
        now = self._clock()
        entry = max(self._queue.values(), key=lambda item: self._score(item, now))
        del self._queue[entry.task_id]
        self._running[entry.task_id] = _Running(entry=entry, started_at=now)
        return entry

    def _score(self, entry: QueueEntry, now: datetime) -> tuple[int, float]:
        age = max(0.0, (now - entry.submitted_at).total_seconds())
        aging = min(age / self._config.aging_seconds, 10.0)
        return (entry.qos.rank, entry.priority + aging)

    # ------------------------------------------------------------------ #
    # Running lifecycle
    # ------------------------------------------------------------------ #
    def mark_checkpoint(self, task_id: str) -> None:
        """Mark a running task as at an idempotent checkpoint (preemptible)."""
        running = self._running.get(task_id)
        if running is not None:
            running.at_checkpoint = True

    def complete(self, task_id: str) -> None:
        """Mark a running task complete, freeing capacity."""
        self._running.pop(task_id, None)

    def requeue(self, task_id: str, *, preempted_by: str | None = None) -> QueueEntry | None:
        """Return a running task to the queue with an extra attempt."""
        running = self._running.pop(task_id, None)
        if running is None:
            return None
        entry = running.entry.model_copy(
            update={
                "attempts": running.entry.attempts + 1,
                "preempted_by": preempted_by,
                "submitted_at": self._clock(),
            }
        )
        self._queue[entry.task_id] = entry
        return entry

    # ------------------------------------------------------------------ #
    # Preemption
    # ------------------------------------------------------------------ #
    def preempt(self, incoming_task_id: str) -> PreemptionRecord | None:
        """Preempt a checkpointed, preemptible victim for urgent guaranteed work.

        Returns ``None`` when no safe victim exists; never preempts mid-side-effect.
        """
        incoming = self._running.get(incoming_task_id)
        if incoming is None or incoming.entry.qos is not QosClass.GUARANTEED:
            return None
        candidates = [
            running
            for running in self._running.values()
            if running.at_checkpoint
            and running.entry.qos.preemptible
            and running.entry.task_id != incoming_task_id
        ]
        if not candidates:
            return None
        victim = min(candidates, key=lambda running: running.entry.qos.rank)
        now = self._clock()
        record = PreemptionRecord(
            victim_task_id=victim.entry.task_id,
            victim_workload=victim.entry.workload,
            victim_qos=victim.entry.qos,
            preempted_by=incoming_task_id,
            reason="preempted by guaranteed work at checkpoint",
            at=now,
        )
        self._preemptions.append(record)
        self.requeue(victim.entry.task_id, preempted_by=incoming_task_id)
        return record

    @property
    def preemptions(self) -> list[PreemptionRecord]:
        """Return the preemption audit trail."""
        return list(self._preemptions)

    # ------------------------------------------------------------------ #
    # Maintenance windows
    # ------------------------------------------------------------------ #
    def freeze(
        self,
        *,
        tenant_id: str | None = None,
        workload: str | None = None,
    ) -> None:
        """Block new admissions for all, a tenant, or a workload."""
        if tenant_id is None and workload is None:
            self._frozen_all = True
        if tenant_id is not None:
            self._frozen_tenants.add(tenant_id)
        if workload is not None:
            self._frozen_workloads.add(workload)

    def unfreeze(
        self,
        *,
        tenant_id: str | None = None,
        workload: str | None = None,
    ) -> None:
        """Lift a maintenance/freeze."""
        if tenant_id is None and workload is None:
            self._frozen_all = False
        if tenant_id is not None:
            self._frozen_tenants.discard(tenant_id)
        if workload is not None:
            self._frozen_workloads.discard(workload)

    def frozen(self, *, workload: str, tenant_id: str) -> bool:
        """Return True when admissions are frozen for the workload/tenant."""
        return self._is_frozen(workload, tenant_id)

    def _is_frozen(self, workload: str, tenant_id: str) -> bool:
        return (
            self._frozen_all
            or tenant_id in self._frozen_tenants
            or workload in self._frozen_workloads
        )

    # ------------------------------------------------------------------ #
    # Visibility
    # ------------------------------------------------------------------ #
    def snapshot(self, *, ctx: TenantContext = SYSTEM_CONTEXT) -> QueueSnapshot:
        """Return queue depth, QoS/priority breakdown, and waiting reasons for the tenant."""
        by_qos: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        waiting: list[WaitingReason] = []
        queue = [
            entry for entry in self._queue.values() if ctx.scopes(entry.tenant_id)
        ]
        for entry in queue:
            by_qos[entry.qos.value] = by_qos.get(entry.qos.value, 0) + 1
            key = str(entry.priority)
            by_priority[key] = by_priority.get(key, 0) + 1
            waiting.append(
                WaitingReason(
                    task_id=entry.task_id,
                    workload=entry.workload,
                    qos=entry.qos,
                    priority=entry.priority,
                    reason="awaiting capacity",
                )
            )
        running_by_workload: dict[str, int] = {}
        running_by_tenant: dict[str, int] = {}
        for running in self._running.values():
            if not ctx.scopes(running.entry.tenant_id):
                continue
            workload = running.entry.workload
            tenant = running.entry.tenant_id
            running_by_workload[workload] = running_by_workload.get(workload, 0) + 1
            running_by_tenant[tenant] = running_by_tenant.get(tenant, 0) + 1
        return QueueSnapshot(
            depth=len(queue),
            by_qos=by_qos,
            by_priority=by_priority,
            waiting=waiting,
            running_by_workload=running_by_workload,
            running_by_tenant=running_by_tenant,
        )

    def _running_for_workload(self, workload: str) -> int:
        return sum(1 for running in self._running.values() if running.entry.workload == workload)

    def _running_for_tenant(self, tenant_id: str) -> int:
        return sum(1 for running in self._running.values() if running.entry.tenant_id == tenant_id)
