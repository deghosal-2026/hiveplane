"""Missed-critique forensics (F-51, F-52): record + suggest deterministic checks.

When a planning failure occurs (the critic missed a flaw that later caused an
execution failure), the forensics module records a :class:`MissedCritique`
with the critique snapshot and auto-suggests a deterministic gate that would
have caught the flaw pre-approval. The suggestion maps LLM-critic reason codes
to the closest deterministic gate, closing the feedback loop: a missed LLM
finding becomes a future deterministic check.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .reason_codes import (
    LLM_FEASIBILITY,
    LLM_MISSING_STEPS,
    LLM_RISK,
    LLM_UNSAFE_SEQUENCING,
    LLM_UNVERIFIED_DEPENDENCIES,
    LLM_WEAK_ROLLBACK,
    ReasonCode,
)
from .store.base import PlanStore
from .types import ExecutionTrace, Finding

#: Map LLM-critic reason codes to the deterministic gate that would catch the
#: same flaw pre-approval. ``None`` means no deterministic gate exists for that
#: heuristic family (e.g. feasibility and risk require model judgment).
_LLM_TO_GATE: dict[ReasonCode, str | None] = {
    LLM_FEASIBILITY: None,
    LLM_RISK: None,
    LLM_MISSING_STEPS: None,
    LLM_UNSAFE_SEQUENCING: "unsafe_parallelization",
    LLM_UNVERIFIED_DEPENDENCIES: "unverified_precondition",
    LLM_WEAK_ROLLBACK: "missing_rollback",
}


class MissedCritique(BaseModel):
    """A record of a critique the LLM critic missed that caused a failure.

    Attributes:
        plan_id: The plan that failed.
        task_id: The task that failed.
        linked_finding_id: The finding the critic produced (as a warning) but
            which turned out to be a real flaw.
        reason_code: The finding's reason code.
        message: The finding's original message.
        suggested_gate: The deterministic gate that would catch this flaw, or
            None if no deterministic gate covers this heuristic family.
    """

    model_config = ConfigDict(frozen=True)

    plan_id: str
    task_id: str
    linked_finding_id: str
    reason_code: ReasonCode
    message: str
    suggested_gate: str | None = Field(
        default=None, description="Deterministic gate that would catch this flaw"
    )

    def persist(self, store: PlanStore) -> None:
        """Serialize and store this record under the plan id.

        Args:
            store: The plan store to persist into.
        """
        store.put_missed_critique(self.plan_id, self.model_dump_json())

    @classmethod
    def load(cls, store: PlanStore, plan_id: str) -> MissedCritique | None:
        """Load a missed-critique record from the store.

        Args:
            store: The plan store to load from.
            plan_id: The plan whose missed-critique to load.

        Returns:
            The record, or None if no missed critique was recorded.
        """
        body = store.get_missed_critique(plan_id)
        if body is None:
            return None
        return cls.model_validate_json(body)


def analyze_failure(trace: ExecutionTrace, finding: Finding) -> MissedCritique:
    """Analyze a planning failure and produce a missed-critique record.

    Args:
        trace: The failed execution trace (``failure_class`` should be
            ``planning``).
        finding: The finding the critic produced but which was only a warning
            (the flaw it flagged caused the execution failure).

    Returns:
        A :class:`MissedCritique` with the critique snapshot and a suggested
        deterministic gate.
    """
    suggested = _LLM_TO_GATE.get(finding.reason_code)
    return MissedCritique(
        plan_id=trace.plan_id,
        task_id=trace.task_id,
        linked_finding_id=finding.id,
        reason_code=finding.reason_code,
        message=finding.message,
        suggested_gate=suggested,
    )


__all__ = ["MissedCritique", "analyze_failure"]
