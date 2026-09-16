"""Sandbox managers: bookkeeping and process-level isolation (D11, DD-14)."""

from __future__ import annotations

import resource
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from hiveplane.core.sandbox import EgressMode, ResourceCaps, SandboxSpec
from hiveplane.sandbox.errors import SandboxNotFoundError
from hiveplane.sandbox.models import SandboxInstance, SandboxStatus

_TERMINAL = (
    SandboxStatus.COMPLETED,
    SandboxStatus.FAILED,
    SandboxStatus.CANCELLED,
    SandboxStatus.DESTROYED,
)


class SandboxManager(Protocol):
    """Provisions, tracks, and destroys sandbox instances."""

    def provision(
        self, *, run_id: str, workload: str, spec: SandboxSpec | None = None
    ) -> SandboxInstance: ...

    def destroy(self, sandbox_id: str) -> None: ...

    def status(self, sandbox_id: str) -> SandboxStatus: ...

    def list_instances(self) -> list[SandboxInstance]: ...

    def reap(self) -> list[str]: ...


class _Bookkeeper:
    """Shared thread-safe sandbox instance bookkeeping."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._instances: dict[str, SandboxInstance] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"sb-{uuid4().hex[:12]}")

    def _save(self, instance: SandboxInstance) -> None:
        with self._lock:
            self._instances[instance.sandbox_id] = instance.model_copy(deep=True)

    def _get(self, sandbox_id: str) -> SandboxInstance:
        with self._lock:
            instance = self._instances.get(sandbox_id)
        if instance is None:
            raise SandboxNotFoundError(sandbox_id)
        return instance

    def _force_status(self, sandbox_id: str, status: SandboxStatus) -> None:
        instance = self._get(sandbox_id)
        self._save(instance.model_copy(update={"status": status}))

    def provision(
        self, *, run_id: str, workload: str, spec: SandboxSpec | None = None
    ) -> SandboxInstance:
        """Create a ready sandbox instance."""
        instance = SandboxInstance(
            sandbox_id=self._id_factory(),
            run_id=run_id,
            workload=workload,
            status=SandboxStatus.READY,
            resource_caps=spec.resource_caps if spec is not None else None,
            egress_mode=spec.egress.mode if spec is not None else EgressMode.RESTRICTED,
            started_at=self._clock(),
        )
        self._save(instance)
        return instance

    def destroy(self, sandbox_id: str) -> None:
        """Tear down a sandbox instance."""
        instance = self._get(sandbox_id)
        self._save(
            instance.model_copy(
                update={"status": SandboxStatus.DESTROYED, "finished_at": self._clock()}
            )
        )

    def status(self, sandbox_id: str) -> SandboxStatus:
        """Return a sandbox's current status."""
        return self._get(sandbox_id).status

    def list_instances(self) -> list[SandboxInstance]:
        """Return all sandbox instances, ordered by start time."""
        with self._lock:
            instances = list(self._instances.values())
        instances.sort(key=lambda item: item.started_at or self._clock())
        return [instance.model_copy(deep=True) for instance in instances]

    def reap(self) -> list[str]:
        """Destroy terminal instances and return their ids."""
        destroyed: list[str] = []
        for instance in self.list_instances():
            if instance.status in _TERMINAL and instance.status is not SandboxStatus.DESTROYED:
                self.destroy(instance.sandbox_id)
                destroyed.append(instance.sandbox_id)
        return destroyed


class InMemorySandboxManager(_Bookkeeper):
    """Bookkeeping-only sandbox manager for local runs and tests."""


def _limit_process(caps: ResourceCaps) -> Callable[[], None]:
    def _apply() -> None:
        _set_soft_limit(resource.RLIMIT_AS, caps.memory_mb * 1024 * 1024)
        _set_soft_limit(resource.RLIMIT_CPU, _cpu_seconds(caps))

    return _apply


def _cpu_seconds(caps: ResourceCaps) -> int:
    """Bound cumulative CPU time by the wall-clock cap.

    ``cpu_cores`` is a rate, not a cumulative limit, so it cannot map onto
    ``RLIMIT_CPU`` (which counts CPU seconds). Until a cgroup/container backend
    enforces core counts (M16-M17), we bound CPU time by the wall-clock cap so a
    spin loop still terminates.
    """
    return max(1, caps.wall_clock_s)


def inspect_caps(caps: ResourceCaps) -> tuple[list[str], list[str]]:
    """Return (applied, errors) for the caps, pre-flighting the OS limits."""
    applied: list[str] = []
    errors: list[str] = []
    checks = (
        ("memory_mb", resource.RLIMIT_AS, caps.memory_mb * 1024 * 1024),
        ("cpu", resource.RLIMIT_CPU, _cpu_seconds(caps)),
    )
    for name, resource_id, target in checks:
        try:
            _, hard = resource.getrlimit(resource_id)
        except (OSError, ValueError):
            errors.append(name)
            continue
        if hard != resource.RLIM_INFINITY and target > hard:
            errors.append(name)
        else:
            applied.append(name)
    return applied, errors


def _set_soft_limit(resource_id: int, soft: int) -> None:
    try:
        _, hard = resource.getrlimit(resource_id)
        target = soft if hard == resource.RLIM_INFINITY else min(soft, hard)
        resource.setrlimit(resource_id, (target, hard))
    except (OSError, ValueError):
        pass


class ProcessSandboxManager(_Bookkeeper):
    """Runs commands in a subprocess with resource caps and guaranteed teardown."""

    def execute(
        self,
        *,
        run_id: str,
        workload: str,
        command: Sequence[str],
        spec: SandboxSpec | None = None,
    ) -> SandboxInstance:
        """Run a command in an isolated subprocess and record the outcome."""
        caps = spec.resource_caps if spec is not None else None
        applied, cap_errors = inspect_caps(caps) if caps is not None else ([], [])
        instance = SandboxInstance(
            sandbox_id=self._id_factory(),
            run_id=run_id,
            workload=workload,
            status=SandboxStatus.RUNNING,
            resource_caps=caps,
            egress_mode=spec.egress.mode if spec is not None else EgressMode.RESTRICTED,
            started_at=self._clock(),
            caps_applied=applied,
            cap_errors=cap_errors,
        )
        self._save(instance)
        scratch = Path(tempfile.mkdtemp(prefix="hiveplane-sandbox-"))
        wall_clock = caps.wall_clock_s if caps is not None else 300
        exit_code: int | None = None
        failure: str | None = None
        try:
            try:
                completed = subprocess.run(
                    list(command),
                    cwd=scratch,
                    env={},
                    capture_output=True,
                    timeout=wall_clock,
                    check=False,
                    preexec_fn=_limit_process(caps) if caps is not None else None,
                )
                exit_code = completed.returncode
                if exit_code != 0:
                    failure = f"exit_code_{exit_code}"
            except subprocess.TimeoutExpired:
                failure = "timeout"
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        status = SandboxStatus.COMPLETED if failure is None else SandboxStatus.FAILED
        result = instance.model_copy(
            update={
                "status": status,
                "finished_at": self._clock(),
                "exit_code": exit_code,
                "failure_reason": failure,
            }
        )
        self._save(result)
        return result
