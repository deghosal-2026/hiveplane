"""Tests for sandbox managers."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from hiveplane.core.sandbox import EgressMode, EgressSpec, ResourceCaps, SandboxSpec
from hiveplane.sandbox.errors import SandboxNotFoundError
from hiveplane.sandbox.manager import InMemorySandboxManager, ProcessSandboxManager
from hiveplane.sandbox.models import SandboxStatus


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _spec(wall_clock_s: int = 5) -> SandboxSpec:
    return SandboxSpec(
        enabled=True,
        resource_caps=ResourceCaps(memory_mb=512, cpu_cores=1.0, wall_clock_s=wall_clock_s),
        egress=EgressSpec(mode=EgressMode.RESTRICTED),
    )


def test_in_memory_provision_destroy_and_reap() -> None:
    ids = iter(["sb-1", "sb-2"])
    manager = InMemorySandboxManager(clock=_clock, id_factory=lambda: next(ids))
    instance = manager.provision(run_id="run-1", workload="agent-1", spec=_spec())
    assert instance.status is SandboxStatus.READY
    assert manager.status(instance.sandbox_id) is SandboxStatus.READY
    manager.destroy(instance.sandbox_id)
    assert manager.status(instance.sandbox_id) is SandboxStatus.DESTROYED
    assert manager.reap() == []
    second = manager.provision(run_id="run-2", workload="agent-1", spec=_spec())
    assert isinstance(second.sandbox_id, str)


def test_status_missing_raises() -> None:
    with pytest.raises(SandboxNotFoundError):
        InMemorySandboxManager(clock=_clock).status("nope")


def test_reap_destroys_terminal_instances() -> None:
    ids = iter(["sb-1", "sb-2"])
    manager = InMemorySandboxManager(clock=_clock, id_factory=lambda: next(ids))
    first = manager.provision(run_id="run-1", workload="agent-1", spec=_spec())
    manager.provision(run_id="run-2", workload="agent-1", spec=_spec())
    manager._force_status(first.sandbox_id, SandboxStatus.COMPLETED)
    assert manager.reap() == [first.sandbox_id]
    assert manager.status(first.sandbox_id) is SandboxStatus.DESTROYED


def test_process_sandbox_completes() -> None:
    manager = ProcessSandboxManager(clock=_clock, id_factory=lambda: "sb-1")
    result = manager.execute(
        run_id="run-1",
        workload="agent-1",
        command=[sys.executable, "-c", "print('ok')"],
        spec=_spec(),
    )
    assert result.status is SandboxStatus.COMPLETED
    assert result.exit_code == 0
    assert result.finished_at is not None


def test_process_sandbox_times_out() -> None:
    manager = ProcessSandboxManager(clock=_clock, id_factory=lambda: "sb-1")
    result = manager.execute(
        run_id="run-1",
        workload="agent-1",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        spec=_spec(wall_clock_s=1),
    )
    assert result.status is SandboxStatus.FAILED
    assert result.failure_reason == "timeout"


def test_process_sandbox_records_exit_code() -> None:
    manager = ProcessSandboxManager(clock=_clock, id_factory=lambda: "sb-1")
    result = manager.execute(
        run_id="run-1",
        workload="agent-1",
        command=[sys.executable, "-c", "import sys; sys.exit(3)"],
        spec=_spec(),
    )
    assert result.status is SandboxStatus.FAILED
    assert result.exit_code == 3
