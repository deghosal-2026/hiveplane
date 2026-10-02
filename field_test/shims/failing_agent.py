"""HivePlane raw-worker shim for the deterministic failure fixtures (S30/S31).

A deliberately failing agent: the entrypoint raises, so the control plane marks
the run ``failed``. Used to exercise the terminal-failure path — the run
``on_failed`` fan-out delivery (S30) and pipeline node retry (S31) — which the
zero-cost local profile cannot trigger through budget exhaustion.
"""

from __future__ import annotations

from typing import Any

from hiveplane.adapters.worker import WorkerContext


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    """Fail deterministically so the run reaches the ``failed`` terminal state."""
    raise RuntimeError("deliberate field-test failure (failing-agent)")
