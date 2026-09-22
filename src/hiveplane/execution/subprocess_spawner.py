"""The subprocess spawner: runs the sandboxed child under resource caps (M23, #110, D11).

The child process is the :mod:`~hiveplane.execution.subprocess_worker` (or any
command), launched in an ephemeral workdir with ``RLIMIT_AS``/``RLIMIT_CPU``
applied via ``preexec_fn`` and a wall-clock watchdog. A capped or timed-out
child yields a failed :class:`SpawnOutcome`; the in-process thread spawner
remains the escape hatch for tests and non-sandboxed runs.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from hiveplane.core.sandbox import ResourceCaps
from hiveplane.sandbox.manager import _limit_process


class SpawnOutcome(BaseModel):
    """The outcome of a capped child process."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int | None = None
    timed_out: bool = False
    failure_reason: str | None = None
    stderr: str | None = None


class SubprocessSpawner:
    """Runs a command in a capped, ephemeral subprocess with guaranteed teardown."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def launch(
        self,
        command: Sequence[str],
        caps: ResourceCaps | None = None,
        *,
        workdir: str | Path | None = None,
    ) -> SpawnOutcome:
        """Run ``command`` under the caps and return the outcome."""
        wall_clock = caps.wall_clock_s if caps is not None else 300
        scratch = (
            Path(workdir)
            if workdir is not None
            else Path(tempfile.mkdtemp(prefix="hiveplane-sandbox-"))
        )
        cleanup = workdir is None
        try:
            return self._run(list(command), caps, scratch, wall_clock)
        finally:
            if cleanup:
                shutil.rmtree(scratch, ignore_errors=True)

    def _run(
        self,
        command: list[str],
        caps: ResourceCaps | None,
        scratch: Path,
        wall_clock: int,
    ) -> SpawnOutcome:
        try:
            completed = subprocess.run(
                command,
                cwd=scratch,
                capture_output=True,
                timeout=wall_clock,
                check=False,
                preexec_fn=_limit_process(caps) if caps is not None else None,
            )
            exit_code = completed.returncode
            return SpawnOutcome(
                exit_code=exit_code,
                timed_out=False,
                failure_reason=None if exit_code == 0 else f"exit_code_{exit_code}",
                stderr=completed.stderr.decode("utf-8", "replace") or None,
            )
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr
            text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
            return SpawnOutcome(
                exit_code=None,
                timed_out=True,
                failure_reason="timeout",
                stderr=text or None,
            )
