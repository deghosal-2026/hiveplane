"""requirement_trace gate — every plan step traces back to an acceptance criterion.

A plan can be safe and well-ordered yet still drift from the user story it was
meant to deliver. This gate checks that every plan step references at least one
acceptance criterion bound to the goal, and flags steps that satisfy no
criterion as ``step_not_traced_to_criterion``.

The finding is a WARNING for backward compatibility — legacy plans without
``satisfies`` fields are not broken, just untraced. Under strict posture the
loop controller will escalate on any finding.
"""

from __future__ import annotations

from ..reason_codes import STEP_NOT_TRACED_TO_CRITERION
from ..schema.plan import PlanVersion
from ..types import Finding, Severity
from .base import BaseGate


class Gate(BaseGate):
    """Flags steps that do not trace to any acceptance criterion."""

    name = "requirement_trace"

    def run(self, plan: PlanVersion) -> list[Finding]:
        """Check plan steps have ``satisfies`` pointers.

        The gate is opt-in: if no task in the plan has a ``satisfies`` field,
        the gate is silent (legacy plan). If at least one task declares
        ``satisfies``, tasks without it are flagged as untraced.

        Args:
            plan: The typed plan to audit.

        Returns:
            One WARNING finding per untraced step (only when the plan has
            opted in by setting ``satisfies`` on at least one task).
        """
        has_any_traced = any(t.satisfies is not None for t in plan.tasks)
        if not has_any_traced:
            return []

        findings: list[Finding] = []
        for task in plan.tasks:
            if task.satisfies is None:
                findings.append(
                    Finding(
                        id=f"requirement_trace:{plan.id}:{plan.version}:{task.id}",
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.WARNING,
                        reason_code=STEP_NOT_TRACED_TO_CRITERION,
                        message=(
                            f"task {task.id!r} does not trace to any acceptance criterion "
                            f"bound to the goal — the plan may be structurally sound but "
                            f"semantically wrong"
                        ),
                        suggested_fix=(
                            f"Add a 'satisfies' field to task {task.id!r} referencing the "
                            f"acceptance criterion it fulfills"
                        ),
                    )
                )
        return findings


__all__ = ["Gate"]
