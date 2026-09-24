"""A deliberately regressed repo agent for the promotion-gate scenario (S4, M23).

It answers every risk-classification task as low risk, so an adapter-backed
certification run fails a critical corpus task and the workload can never be
promoted to production. It exists only to prove the benchmark is not theater.
"""

from __future__ import annotations

from typing import Any

from hiveplane.adapters.worker import WorkerContext


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    """Return a confident-but-wrong risk assessment for every task."""
    return {"risk": "low", "summary": "looks fine"}
