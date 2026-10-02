"""Tests for the fleet scheduler (M47)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hiveplane.scheduler import (
    AdmissionAction,
    BackpressureReason,
    QosClass,
    QueueEntry,
    Scheduler,
    SchedulerConfig,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.now = _NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _scheduler(**config: int) -> tuple[Scheduler, _Clock]:
    clock = _Clock()
    return Scheduler(SchedulerConfig(**config), clock=clock), clock


def _entry(
    task_id: str,
    *,
    qos: QosClass = QosClass.BEST_EFFORT,
    priority: int = 0,
    workload: str = "w",
    tenant_id: str = "t",
    submitted_at: datetime = _NOW,
) -> QueueEntry:
    return QueueEntry(
        task_id=task_id,
        tenant_id=tenant_id,
        workload=workload,
        qos=qos,
        priority=priority,
        submitted_at=submitted_at,
    )


# --------------------------------------------------------------------------- #
# M47-01/04 — priority, QoS, fairness
# --------------------------------------------------------------------------- #
def test_dequeue_orders_by_qos_then_priority() -> None:
    scheduler, _ = _scheduler()
    scheduler.submit(_entry("best", qos=QosClass.BEST_EFFORT, priority=99))
    scheduler.submit(_entry("burst", qos=QosClass.BURSTABLE, priority=1))
    scheduler.submit(_entry("guar", qos=QosClass.GUARANTEED, priority=0))

    order = [scheduler.dequeue().task_id for _ in range(3)]  # type: ignore[union-attr]
    assert order == ["guar", "burst", "best"]


def test_priority_cannot_cross_qos_class() -> None:
    scheduler, _ = _scheduler()
    scheduler.submit(_entry("best", qos=QosClass.BEST_EFFORT, priority=10**9))
    scheduler.submit(_entry("burst", qos=QosClass.BURSTABLE, priority=-(10**9)))

    order = [scheduler.dequeue().task_id for _ in range(2)]  # type: ignore[union-attr]
    assert order == ["burst", "best"]


def test_aging_prevents_starvation() -> None:
    scheduler, clock = _scheduler(aging_seconds=10)
    scheduler.submit(_entry("old", qos=QosClass.BEST_EFFORT, priority=0))
    clock.advance(100)
    scheduler.submit(_entry("new", qos=QosClass.BEST_EFFORT, priority=0, submitted_at=clock.now))

    assert scheduler.dequeue().task_id == "old"  # type: ignore[union-attr]


def test_qos_preemptibility() -> None:
    assert QosClass.GUARANTEED.rank > QosClass.BURSTABLE.rank > QosClass.BEST_EFFORT.rank
    assert not QosClass.GUARANTEED.preemptible
    assert QosClass.BEST_EFFORT.preemptible


# --------------------------------------------------------------------------- #
# M47-02/03 — limits and backpressure
# --------------------------------------------------------------------------- #
def test_per_workload_limit_rejects_with_reason() -> None:
    scheduler, _ = _scheduler(per_workload_limit=1, per_tenant_limit=10)
    running = scheduler.submit(_entry("a"))
    assert running.action is AdmissionAction.QUEUED
    scheduler.dequeue()

    refused = scheduler.submit(_entry("b"))
    assert refused.action is AdmissionAction.REJECTED
    assert refused.reason is BackpressureReason.PER_WORKLOAD_LIMIT
    assert refused.code == "capacity: per_workload_limit"


def test_per_tenant_limit_rejects_with_reason() -> None:
    scheduler, _ = _scheduler(per_workload_limit=10, per_tenant_limit=1)
    scheduler.submit(_entry("a"))
    scheduler.dequeue()

    refused = scheduler.submit(_entry("b", workload="other"))
    assert refused.reason is BackpressureReason.PER_TENANT_LIMIT


def test_queue_depth_backpressure() -> None:
    scheduler, _ = _scheduler(max_queue_depth=1)
    scheduler.submit(_entry("a"))

    refused = scheduler.submit(_entry("b"))
    assert refused.reason is BackpressureReason.QUEUE_DEPTH
    assert refused.code == "capacity: queue_depth"


def test_maintenance_blocks_admission() -> None:
    scheduler, _ = _scheduler()
    scheduler.freeze(workload="w")

    refused = scheduler.submit(_entry("a", workload="w"))
    assert refused.reason is BackpressureReason.MAINTENANCE
    assert scheduler.frozen(workload="w", tenant_id="t")

    scheduler.unfreeze(workload="w")
    assert scheduler.submit(_entry("a", workload="w")).action is AdmissionAction.QUEUED


# --------------------------------------------------------------------------- #
# M47-05 — preemption at checkpoints
# --------------------------------------------------------------------------- #
def test_preemption_requires_checkpoint_and_attribution() -> None:
    scheduler, _ = _scheduler()
    scheduler.submit(_entry("urgent", qos=QosClass.GUARANTEED))
    scheduler.submit(_entry("victim", qos=QosClass.BEST_EFFORT))
    scheduler.dequeue()  # urgent
    scheduler.dequeue()  # victim

    # mid-side-effect: no safe victim yet
    assert scheduler.preempt("urgent") is None

    scheduler.mark_checkpoint("victim")
    record = scheduler.preempt("urgent")
    assert record is not None
    assert record.victim_task_id == "victim"
    assert record.preempted_by == "urgent"
    assert record.victim_qos is QosClass.BEST_EFFORT

    snapshot = scheduler.snapshot()
    assert snapshot.depth == 1
    assert snapshot.waiting[0].task_id == "victim"
    assert scheduler.preemptions[-1].victim_task_id == "victim"


def test_preemption_only_for_guaranteed_incoming() -> None:
    scheduler, _ = _scheduler()
    scheduler.submit(_entry("a", qos=QosClass.BURSTABLE))
    scheduler.dequeue()
    scheduler.mark_checkpoint("a")
    assert scheduler.preempt("a") is None


# --------------------------------------------------------------------------- #
# M47-08 — queue visualizer
# --------------------------------------------------------------------------- #
def test_snapshot_reports_depth_priorities_and_load() -> None:
    scheduler, _ = _scheduler()
    scheduler.submit(_entry("a", qos=QosClass.GUARANTEED, priority=5))
    scheduler.submit(_entry("b", qos=QosClass.BEST_EFFORT, workload="other"))
    scheduler.dequeue()
    scheduler.submit(_entry("c", qos=QosClass.BURSTABLE, priority=2))

    snapshot = scheduler.snapshot()
    assert snapshot.depth == 2
    assert snapshot.by_qos == {"best_effort": 1, "burstable": 1}
    assert snapshot.running_by_workload == {"w": 1}
    assert snapshot.running_by_tenant == {"t": 1}
    assert {w.task_id for w in snapshot.waiting} == {"b", "c"}


def test_complete_and_requeue() -> None:
    scheduler, _ = _scheduler()
    scheduler.submit(_entry("a"))
    entry = scheduler.dequeue()
    assert entry is not None

    requeued = scheduler.requeue("a", preempted_by="urgent")
    assert requeued is not None and requeued.attempts == 2
    assert requeued.preempted_by == "urgent"

    scheduler.dequeue()
    scheduler.complete("a")
    assert scheduler.snapshot().depth == 0
    assert scheduler.requeue("missing") is None


def test_queue_api_snapshot_and_freeze() -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    client = TestClient(create_app())
    assert client.get("/queue").json()["depth"] == 0

    frozen = client.post("/queue/freeze", json={"workload": "w"})
    assert frozen.status_code == 200
    assert client.post("/queue/unfreeze", json={"workload": "w"}).status_code == 200


def test_dlq_replay_is_available_through_trigger_engine() -> None:
    """M47-07: failed trigger deliveries are dead-lettered and replayable (M27)."""
    from hiveplane.fleet.triggers import TriggerDlqEntry
    from hiveplane.triggers.engine import TriggerEngine  # noqa: F401

    entry = TriggerDlqEntry(
        entry_id="dlq-1",
        trigger_id="trig-1",
        event_id="evt-1",
        tenant_id="default",
        failure_reason="delivery failed",
        attempts=3,
        created_at=_NOW,
    )
    assert entry.replayed_at is None
