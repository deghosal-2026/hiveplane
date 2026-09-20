"""schema_valid gate — the plan parses against the typed schema.

Structural validation that cannot be expressed in the Pydantic model itself:
blank ids/descriptions, empty task lists, empty plans, and a mismatched
``plan_schema_version``. Pure code; injection-immune (F-12).
"""

from __future__ import annotations

from ..reason_codes import PLAN_SCHEMA_INVALID
from ..schema.plan import PLAN_SCHEMA_VERSION, PlanVersion
from ..types import Finding, Severity
from .base import BaseGate


class Gate(BaseGate):
    """Flags plans that are structurally malformed despite being typed."""

    name = "schema_valid"

    def run(self, plan: PlanVersion) -> list[Finding]:
        """Check schema-level validity of the plan.

        Args:
            plan: The typed plan to audit.

        Returns:
            A single finding when the plan is structurally invalid; empty
            when it is valid.
        """
        findings: list[Finding] = []

        if not plan.tasks:
            findings.append(
                self._finding(plan, "plan has no tasks; a plan must contain at least one task")
            )
        if any(not t.id.strip() for t in plan.tasks):
            findings.append(self._finding(plan, "plan contains a task with a blank id"))
        if plan.plan_schema_version != PLAN_SCHEMA_VERSION:
            findings.append(
                self._finding(
                    plan,
                    f"plan_schema_version {plan.plan_schema_version!r} is unsupported; "
                    f"expected {PLAN_SCHEMA_VERSION!r}",
                )
            )
        return findings

    def _finding(self, plan: PlanVersion, message: str) -> Finding:
        """Build a schema-validity finding for the plan."""
        return Finding(
            id=f"schema_valid:{plan.id}:{plan.version}",
            version=plan.version,
            severity=Severity.BLOCKER,
            reason_code=PLAN_SCHEMA_INVALID,
            message=message,
            suggested_fix="Reconstruct the plan against the typed schema and re-draft it",
        )
