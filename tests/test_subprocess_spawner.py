"""Tests for the SubprocessSpawner and the sandboxed child caps (M23, #110c).

Proves the two hard exit criteria: a memory hog is capped by RLIMIT_AS (the
child applies the limit and the allocation fails) and a wall-clock exceeder is
killed by the spawner's watchdog. A clean child still completes.

RLIMIT_AS can only be lowered below the current address space on Linux; macOS
forbids it ("current limit exceeds maximum limit"), so the memory cap is
enforced on Linux/Docker and is a best-effort no-op elsewhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hiveplane.core.sandbox import ResourceCaps
from hiveplane.execution.sandbox_spec import SandboxWorkerSpec, worker_command
from hiveplane.execution.subprocess_spawner import SubprocessSpawner

_CAPS = ResourceCaps(memory_mb=1024, cpu_cores=1.0, wall_clock_s=30)
_SPIN = [sys.executable, "-c", "import time; time.sleep(30)"]
_CLEAN = [sys.executable, "-c", "print('ok')"]

_LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="RLIMIT_AS memory cap is Linux-only"
)


@_LINUX_ONLY
def test_memory_hog_is_capped_by_rlimit_as(tmp_path: Path) -> None:
    (tmp_path / "hog.py").write_text(
        "def run(task, ctx):\n    bytearray(4 * 10**9)\n", encoding="utf-8"
    )
    spec = SandboxWorkerSpec(
        run_id="run-hog",
        entrypoint="hog:run",
        task={},
        base_url="http://127.0.0.1:9/",
        token="tok",
        root=str(tmp_path),
        resource_caps=ResourceCaps(memory_mb=1024, cpu_cores=1.0, wall_clock_s=30),
    )

    outcome = SubprocessSpawner().launch(worker_command(spec), spec.resource_caps)

    assert outcome.timed_out is False
    assert outcome.exit_code not in (0, None) or outcome.failure_reason is not None, (
        "memory hog was not capped"
    )


def test_wall_clock_exceeder_is_killed_by_the_watchdog() -> None:
    caps = ResourceCaps(memory_mb=1024, cpu_cores=1.0, wall_clock_s=1)

    outcome = SubprocessSpawner().launch(_SPIN, caps)

    assert outcome.timed_out is True
    assert outcome.failure_reason == "timeout"


def test_clean_child_completes() -> None:
    outcome = SubprocessSpawner().launch(_CLEAN, _CAPS)

    assert outcome.timed_out is False
    assert outcome.failure_reason is None
    assert outcome.exit_code == 0
