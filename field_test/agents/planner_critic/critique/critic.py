"""The six-heuristic LLM critic role (F-04, PRD §2.5.1).

Beyond the deterministic gates, the critic audits a plan across six heuristic
families — feasibility, risk, missing steps, unsafe sequencing, unverified
dependencies, weak rollback. It calls a provider through the structured-output
enforcer and maps the structured response to typed
:class:`~planner_critic.types.Finding` objects with catalog reason codes.

A finding always carries: heuristic family, severity (blocker/warning/info),
a task reference, a machine-readable reason code, a message, and an optional
suggested fix. A blocker from a deterministic gate can never be overridden by
the LLM critic (injection-safety, §2.5.1) — the critic only *adds* findings.

The :class:`LLMCritic` binds a :class:`Goal` at construction: a critic audits
the revisions of one goal (the loop passes only ``(plan, findings)`` through
the :class:`CriticRole` protocol), so the bound goal supplies the prompt
context.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..llm.base import LLMProvider, Message
from ..llm.structured import StructuredEnforcer
from ..reason_codes import (
    LLM_FEASIBILITY,
    LLM_MISSING_STEPS,
    LLM_RISK,
    LLM_UNSAFE_SEQUENCING,
    LLM_UNVERIFIED_DEPENDENCIES,
    LLM_WEAK_ROLLBACK,
    ReasonCode,
)
from ..schema.goal import Goal
from ..schema.plan import PlanVersion
from ..types import Finding, HeuristicFamily, Severity
from .diff import dependent_closure


class CritiqueItem(BaseModel):
    """One structured critique result from the model.

    Attributes:
        heuristic_family: Which heuristic family produced this finding.
        severity: blocker / warning / info.
        task_id: The task id the finding targets, or null for plan-level.
        message: Human-readable description of the problem.
        suggested_fix: Optional remediation.
    """

    model_config = ConfigDict(frozen=True)

    heuristic_family: str = Field(description="One of the six heuristic families")
    severity: str = Field(description="blocker | warning | info")
    task_id: str | None = Field(default=None, description="Target task id")
    message: str = Field(min_length=1, description="What is wrong")
    suggested_fix: str | None = Field(default=None)


class CritiqueOutput(BaseModel):
    """The model's full structured critique response."""

    model_config = ConfigDict(frozen=True)

    findings: list[CritiqueItem] = Field(default_factory=list)


# Map a heuristic family name to its catalog reason code.
_FAMILY_TO_CODE: dict[str, ReasonCode] = {
    "feasibility": LLM_FEASIBILITY,
    "risk": LLM_RISK,
    "missing_steps": LLM_MISSING_STEPS,
    "unsafe_sequencing": LLM_UNSAFE_SEQUENCING,
    "unverified_dependencies": LLM_UNVERIFIED_DEPENDENCIES,
    "weak_rollback": LLM_WEAK_ROLLBACK,
}

_SYSTEM_PROMPT = (
    "/no_think You are a plan reviewer. Reply with ONLY a JSON "
    "object (no markdown, no prose, no thinking). Audit the given plan against "
    "six heuristic families: feasibility, risk, missing_steps, "
    "unsafe_sequencing, unverified_dependencies, weak_rollback. For each "
    "problem, return a finding with heuristic_family (one of those six names), "
    "severity (blocker, warning, or info), task_id (the task the problem "
    "affects, or omit for plan-level), message (specific, actionable), and "
    "suggested_fix (optional). Return JSON with a top-level 'findings' array.\n\n"
    "SEVERITY RULES — blocker is reserved ONLY for concrete, plan-local defects "
    "that make the plan unsafe to execute:\n"
    "  blocker = unsafe_sequencing (a task ordered before its hard prerequisite), "
    "weak_rollback (a high-blast-radius step lacks sound rollback), "
    "unverified_dependencies (a step depends on a fact never established by an "
    "earlier task), or feasibility (a task is not achievable with the stated "
    "environment/tools).\n"
    "  warning = risk (generic risk/blast-radius commentary), missing_steps "
    "(completeness suggestions, 'could also cover X', edge-case omissions that "
    "do not make the plan concretely unsafe).\n"
    "  info = minor observations.\n"
    "Do NOT escalate completeness or thoroughness concerns to blocker. If the "
    "plan is structurally sound with correct ordering, verification, and "
    "rollback, it should produce zero blockers."
)


def _build_messages(goal: Goal, plan: PlanVersion, scope: Sequence[str] | None) -> list[Message]:
    """Build the critic prompt for a goal + plan (optionally scoped tasks).

    Args:
        goal: The typed goal being planned.
        plan: The plan revision to audit.
        scope: Optional task ids to restrict the audit to (diff-aware); None
            audits the whole plan.

    Returns:
        The system + user messages for the provider.
    """
    if scope is not None:
        scope_tasks = [t for t in plan.tasks if t.id in set(scope)]
        scope_clause = "Audit ONLY these tasks: " + ", ".join(t.id for t in scope_tasks) + "."
    else:
        scope_clause = "Audit the entire plan."
    user_text = (
        f"GOAL:\n{goal.model_dump(mode='json')}\n\n"
        f"PLAN:\n{plan.model_dump(mode='json')}\n\n"
        f"{scope_clause}\n\n"
        'Respond with JSON: {"findings": [...]}.'
    )
    return [
        Message(role="system", content=_SYSTEM_PROMPT),
        Message(role="user", content=user_text),
    ]


class LLMCritic:
    """A :class:`CriticRole` backed by a configured provider (F-04).

    Args:
        goal: The goal whose revisions this critic audits.
        provider: The transport to call for critique.
        max_retries: Bounded structured-output retries before fail-closed.
    """

    def __init__(self, goal: Goal, provider: LLMProvider, max_retries: int = 2) -> None:
        """Bind the goal, the provider, and the retry budget."""
        self.goal = goal
        self.provider = provider
        self._enforcer = StructuredEnforcer(provider, max_retries=max_retries)

    def audit(self, plan: PlanVersion, findings: list[Finding]) -> list[Finding]:
        """Audit ``plan`` for the bound goal; append heuristic findings.

        Args:
            plan: The current plan revision.
            findings: Findings collected so far (gates); the critic appends.

        Returns:
            The complete finding list including the critic's additions.
        """
        return self._audit(plan, findings, scope=None)

    def audit_diff(
        self,
        plan: PlanVersion,
        findings: list[Finding],
        changed_task_ids: Sequence[str],
    ) -> list[Finding]:
        """Audit only changed tasks + their dependents (F-78, §2.5.3).

        Args:
            plan: The current plan revision.
            findings: Findings collected so far.
            changed_task_ids: The changed-task set from the plan diff.

        Returns:
            The complete finding list including the scoped critic additions.
        """
        scope = dependent_closure(plan, changed_task_ids)
        return self._audit(plan, findings, scope=scope)

    def _audit(
        self,
        plan: PlanVersion,
        findings: list[Finding],
        scope: Sequence[str] | None,
    ) -> list[Finding]:
        """Run the critique and append mapped findings."""
        messages = _build_messages(self.goal, plan, scope)
        output = self._enforcer.complete(messages, CritiqueOutput)
        return [*findings, *(_to_findings(plan.version, output.findings))]


# Families eligible for blocker severity — concrete safety/ordering/rollback/feasibility defects.
_BLOCKER_ELIGIBLE_FAMILIES: frozenset[str] = frozenset(
    {
        "unsafe_sequencing",
        "weak_rollback",
        "unverified_dependencies",
        "feasibility",
    }
)


def _to_findings(version: int, items: Sequence[CritiqueItem]) -> list[Finding]:
    """Map structured critique items to typed Findings with reason codes.

    Args:
        version: The plan revision the findings were produced against.
        items: The model's structured critique results.

    Returns:
        Mapped findings. Unknown heuristic families or severities are skipped
        rather than trusted. A severity guardrail downgrades advisory families
        (risk, missing_steps) from blocker to warning — completeness concerns
        must not be fatal under strict tolerance.
    """
    result: list[Finding] = []
    for item in items:
        code = _FAMILY_TO_CODE.get(item.heuristic_family)
        if code is None:
            continue
        family = _family_from_name(item.heuristic_family)
        severity = _severity_from_name(item.severity)
        if family is None or severity is None:
            continue
        if severity == Severity.BLOCKER and item.heuristic_family not in _BLOCKER_ELIGIBLE_FAMILIES:
            severity = Severity.WARNING
        finding_id = f"crit:{version}:{item.heuristic_family}:{item.task_id or 'plan'}"
        result.append(
            Finding(
                id=finding_id,
                task_id=item.task_id,
                version=version,
                heuristic_family=family,
                severity=severity,
                reason_code=code,
                message=item.message,
                suggested_fix=item.suggested_fix,
            )
        )
    return result


def _family_from_name(name: str) -> HeuristicFamily | None:
    """Map a family name to its :class:`HeuristicFamily` enum."""
    for family in HeuristicFamily:
        if family.value == name:
            return family
    return None


def _severity_from_name(name: str) -> Severity | None:
    """Map a severity name to its :class:`Severity` enum."""
    for value in Severity:
        if value.value == name:
            return value
    return None
