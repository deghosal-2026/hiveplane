"""Trace replay (F-76): walk plan version history step by step.

The replay function walks every stored revision of a plan, collecting the
plan version and its critique findings into ordered :class:`ReplayStep`
objects. The result can be rendered as JSON (``--format json``) or walked
step-by-step (``--step N`` limits the depth).
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..schema.plan import PlanVersion
from ..store.base import PlanStore
from ..types import Finding


class ReplayStep(BaseModel):
    """One revision in the replay history."""

    model_config = ConfigDict(frozen=True)

    version: int
    plan: PlanVersion
    findings: list[Finding]


class ReplayResult:
    """The full replay history for a plan, walkable and serializable."""

    def __init__(self, plan_id: str, steps: list[ReplayStep]) -> None:
        """Initialize with the plan id and ordered replay steps."""
        self.plan_id = plan_id
        self.steps = steps

    def to_json(self) -> str:
        """Serialize the replay to a JSON string."""
        return json.dumps(
            {
                "plan_id": self.plan_id,
                "steps": [
                    {
                        "version": s.version,
                        "plan": s.plan.model_dump(mode="json"),
                        "findings": [f.model_dump(mode="json") for f in s.findings],
                    }
                    for s in self.steps
                ],
            }
        )


def replay(
    store: PlanStore,
    plan_id: str,
    step: int | None = None,
    fmt: Literal["text", "json"] = "text",
) -> ReplayResult:
    """Walk the version history of a plan.

    Args:
        store: The plan store to read from.
        plan_id: The plan to replay.
        step: Optional limit on the number of steps (revisions) to return.
        fmt: Output format (``text`` or ``json``); only affects how the
            caller renders the result.

    Returns:
        A :class:`ReplayResult` with ordered steps, newest last.
    """
    plans = store.list_plans(goal_id=None)
    relevant = [p for p in plans if p.id == plan_id]
    relevant.sort(key=lambda p: p.version)

    if step is not None:
        relevant = relevant[:step]

    steps: list[ReplayStep] = []
    for plan in relevant:
        findings = _get_findings(store, plan.id, plan.version)
        steps.append(ReplayStep(version=plan.version, plan=plan, findings=findings))

    result = ReplayResult(plan_id, steps)
    if fmt == "json":
        result.to_json()
    return result


def _get_findings(store: PlanStore, plan_id: str, version: int) -> list[Finding]:
    """Fetch findings for a revision through the store protocol.

    Both store implementations persist findings under ``(plan_id, version)``
    and expose :meth:`PlanStore.get_findings`; the private ``_findings`` index
    is a legacy in-memory shortcut and is no longer consulted. This fixes the
    C22/C15 field-test gap where SQLite stores returned empty replay traces.
    """
    return list(store.get_findings(plan_id, version))


__all__ = ["ReplayResult", "ReplayStep", "replay"]
