"""Tests for the SubprocessSpawner and the sandboxed child caps (M23, #110c).

Proves the two hard exit criteria: a memory hog is capped by RLIMIT_AS (the
child applies the limit and the allocation fails) and a wall-clock exceeder is
killed by the spawner's watchdog. A clean child still completes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hiveplane.core.sandbox import ResourceCaps
from hiveplane.execution.subprocess_spawner import SubprocessSpawner
from hiveplane.execution.subprocess_worker import SandboxWorkerSpec

_CAPS = ResourceCaps(memory_mb=1024, cpu_cores=1.0, wall_clock_s=30)
_SPIN = [sys.executable, "-c", "import time; time.sleep(30)"]
_CLEAN = [sys.executable, "-c", "print('ok')"]


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
    command = [
        sys.executable,
        "-m",
        "hiveplane.execution.subprocess_worker",
        "--spec",
        spec.model_dump_json(),
    ]

    outcome = SubprocessSpawner().launch(command, spec.resource_caps)

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


def test_child_rejects_an_unapplicable_memory_cap(tmp_path: Path) -> None:
    # A cap below the interpreter baseline cannot be applied; the run must fail
    # loudly rather than run uncapped.
    (tmp_path / "noop.py").write_text(
        "def run(task, ctx):\n    return {'ok': True}\n", encoding="utf-8"
    )
    spec = SandboxWorkerSpec(
        run_id="run-tight",
        entrypoint="noop:run",
        task={},
        base_url="http://127.0.0.1:9/",
        token="tok",
        root=str(tmp_path),
        resource_caps=ResourceCaps(memory_mb=8, cpu_cores=1.0, wall_clock_s=30),
    )
    command = [
        sys.executable,
        "-m",
        "hiveplane.execution.subprocess_worker",
        "--spec",
        spec.model_dump_json(),
    ]

    outcome = SubprocessSpawner().launch(command, spec.resource_caps)

    assert outcome.exit_code not in (0, None)
