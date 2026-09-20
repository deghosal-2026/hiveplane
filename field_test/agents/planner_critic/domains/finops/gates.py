"""FinOps domain gates (M4, #142).

Two domain-specific deterministic gates:
- GracePeriodGate — instant delete without snapshot+notify+wait
- BudgetBoundaryGate — expansion breaches localized budget cap
"""

from __future__ import annotations

from planner_critic.gates.base import BaseGate
from planner_critic.reason_codes import (
    FINOPS_BUDGET_BOUNDARY_BREACHED,
    FINOPS_DELETE_WITHOUT_GRACE_PERIOD,
)
from planner_critic.schema.plan import PlanVersion
from planner_critic.types import Finding, Severity

DELETE_ACTIONS = frozenset({"delete", "terminate", "release"})
SNAPSHOT_ACTIONS = frozenset({"snapshot", "backup"})
NOTIFY_ACTIONS = frozenset({"notify_owner", "notify"})
WAIT_ACTIONS = frozenset({"wait_grace_period", "wait"})
OVERRIDE_ACTIONS = frozenset({"executive_override", "budget_override"})


class GracePeriodGate(BaseGate):
    """Flags deletes without a prior snapshot + owner-notify + grace wait."""

    name = "finops_grace_period"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        has_snapshot = any(t.action in SNAPSHOT_ACTIONS for t in plan.tasks)
        has_notify = any(t.action in NOTIFY_ACTIONS for t in plan.tasks)
        has_wait = any(t.action in WAIT_ACTIONS for t in plan.tasks)
        for task in plan.tasks:
            if task.action in DELETE_ACTIONS and not (has_snapshot and has_notify and has_wait):
                findings.append(
                    Finding(
                        id=(f"finops_grace_period:{plan.id}:{plan.version}:{task.id}"),
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=FINOPS_DELETE_WITHOUT_GRACE_PERIOD,
                        message=(
                            f"delete step {task.id!r} lacks the full grace "
                            f"period: snapshot={has_snapshot}, "
                            f"notify={has_notify}, wait={has_wait}"
                        ),
                        suggested_fix=(
                            "Add snapshot, notify_owner, and wait_grace_period "
                            "steps before the delete"
                        ),
                    )
                )
        return findings


class BudgetBoundaryGate(BaseGate):
    """Flags scale-up that breaches the localized budget cap."""

    name = "finops_budget_boundary"

    def __init__(self, budget_cap: float = 100_000) -> None:
        super().__init__()
        self.budget_cap = budget_cap

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        has_override = any(t.action in OVERRIDE_ACTIONS for t in plan.tasks)

        spend = 0.0
        scale_up_tasks = 0
        for task in plan.tasks:
            if task.action in ("scale_up", "scale_out", "provision", "add_capacity"):
                scale_up_tasks += 1
                # Parse cost from target if numeric; otherwise skip
                # (cost metadata deferred to v0.3.0)
                try:
                    spend += float(task.target or 0)
                except ValueError:
                    pass

        # Flag when scale-ups exist without override and either spend exceeds cap
        # or cost data is missing (target is non-numeric)
        if not has_override:
            if scale_up_tasks > 0 and spend <= 0:
                findings.append(
                    Finding(
                        id=f"finops_budget_boundary:{plan.id}:{plan.version}",
                        version=plan.version,
                        severity=Severity.WARNING,
                        reason_code=FINOPS_BUDGET_BOUNDARY_BREACHED,
                        message=(
                            f"{scale_up_tasks} scale-up action(s) without cost "
                            "data or executive override"
                        ),
                    )
                )
            elif spend > self.budget_cap:
                findings.append(
                    Finding(
                        id=f"finops_budget_boundary:{plan.id}:{plan.version}",
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=FINOPS_BUDGET_BOUNDARY_BREACHED,
                        message=(
                            f"scale-up spend {spend:.0f} exceeds localized cap "
                            f"{self.budget_cap:.0f} without executive override"
                        ),
                        suggested_fix=("Add an executive_override step or stay within the cap"),
                    )
                )
        return findings


FINOPS_PRECONDITIONS = {
    "snapshot_created": "A snapshot of the resource exists",
    "owner_notified": "The resource owner has been notified",
    "grace_period_elapsed": "The grace period has elapsed",
    "budget_within_cap": "Spend is within the localized cap",
    "spend_forecast_checked": "The spend forecast has been checked",
}

FINOPS_CRITIC_PROMPT = (
    "Audit this plan from a FinOps / cost-governance perspective. "
    "Pay attention to: (1) instant resource deletes without a snapshot, "
    "owner notification, and grace-period wait, "
    "(2) scale-up operations that breach localized budget caps "
    "without an executive override.\n"
)


__all__ = [
    "FINOPS_CRITIC_PROMPT",
    "FINOPS_PRECONDITIONS",
    "BudgetBoundaryGate",
    "GracePeriodGate",
]
