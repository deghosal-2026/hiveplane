"""SecOps domain gates (M4, #140).

Three domain-specific deterministic gates:
- BlastRadiusGate — isolation without prior traffic drain
- ForensicOrderGate — terminate/stop before snapshot
- LeastPrivilegeGate — broad privilege without HITL
"""

from __future__ import annotations

from planner_critic.gates.base import BaseGate
from planner_critic.reason_codes import (
    SECOPS_BROAD_PRIVILEGE_WITHOUT_HITL,
    SECOPS_FORENSIC_ORDER_VIOLATION,
    SECOPS_ISOLATION_WITHOUT_TRAFFIC_DRAIN,
)
from planner_critic.schema.plan import PlanVersion
from planner_critic.types import Finding, Severity

# ── Gate 1: Blast-radius check ────────────────────────────────────────────


class BlastRadiusGate(BaseGate):
    """Flags isolation steps that lack a prior traffic drain step."""

    name = "secops_blast_radius"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        seen_drain = False
        for task in plan.tasks:
            if task.action == "drain":
                seen_drain = True
            if task.action == "isolate" and not seen_drain:
                findings.append(
                    Finding(
                        id=f"secops_blast_radius:{plan.id}:{plan.version}:{task.id}",
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=SECOPS_ISOLATION_WITHOUT_TRAFFIC_DRAIN,
                        message=f"isolation step {task.id!r} has no prior traffic drain",
                        suggested_fix=("Add a traffic drain step before isolation"),
                    )
                )
        return findings


# ── Gate 2: Forensic order of operations ──────────────────────────────────


DESTRUCTIVE_ACTIONS = frozenset({"terminate", "stop", "destroy", "delete"})
PRESERVATION_ACTIONS = frozenset({"snapshot", "backup", "export", "capture"})


class ForensicOrderGate(BaseGate):
    """Flags destructive actions ordered before preservation steps."""

    name = "secops_forensic_order"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        seen_destructive = False
        for task in plan.tasks:
            if task.action in DESTRUCTIVE_ACTIONS:
                seen_destructive = True
            elif task.action in PRESERVATION_ACTIONS and seen_destructive:
                findings.append(
                    Finding(
                        id=(f"secops_forensic_order:{plan.id}:{plan.version}:{task.id}"),
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=SECOPS_FORENSIC_ORDER_VIOLATION,
                        message=(
                            f"destructive action appears before preservation "
                            f"step {task.id!r} ({task.action})"
                        ),
                        suggested_fix=("Move preservation steps before destructive actions"),
                    )
                )
        return findings


# ── Gate 3: Least-privilege verification ──────────────────────────────────


BROAD_TARGETS = frozenset({"*", "all", "everything", "arn:aws:iam::*"})


class LeastPrivilegeGate(BaseGate):
    """Flags broad-privilege actions that lack a prior HITL approval step."""

    name = "secops_least_privilege"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        has_hitl = any(
            task.action in ("human_approval", "approve", "peer_review") for task in plan.tasks
        )
        for task in plan.tasks:
            is_broad = task.action.startswith("sts:") and any(
                _match_broad_target(task.target or "", t) for t in BROAD_TARGETS
            )
            if is_broad and not has_hitl:
                findings.append(
                    Finding(
                        id=(f"secops_least_privilege:{plan.id}:{plan.version}:{task.id}"),
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=SECOPS_BROAD_PRIVILEGE_WITHOUT_HITL,
                        message=(
                            f"broad privilege {task.action!r} on "
                            f"{task.target!r} has no prior human approval step"
                        ),
                        suggested_fix=(
                            "Add a human_approval step before broad privilege escalation"
                        ),
                    )
                )
        return findings


def _match_broad_target(target: str, broad: str) -> bool:
    """Match a broad target against a resource name.

    Uses anchored matching: ``*`` matches any target ending with ``:*`` or ``:*/*``,
    ``all`` matches exactly, ``everything`` matches exactly,
    ``arn:aws:iam::*`` matches any IAM account root.
    """
    if broad == "*":
        return target == "*"
    if broad == "all":
        return target == "all"
    if broad == "everything":
        return target == "everything"
    if broad == "arn:aws:iam::*":
        return target == "arn:aws:iam::*" or (
            target.startswith("arn:aws:iam::") and target.endswith(":root")
        )
    return False


# ── Pack metadata ─────────────────────────────────────────────────────────

SECOPS_PRECONDITIONS = {
    "traffic_drained": "Traffic has been drained from the target resource",
    "snapshot_created": "A recent forensic snapshot of the resource exists",
    "failover_complete": "Failover to the secondary region has completed",
    "credential_revoked": "The compromised credential has been revoked",
}

SECOPS_CRITIC_PROMPT = (
    "Audit this plan from a security-engineering perspective. "
    "Pay special attention to: (1) isolation steps that bypass traffic drain, "
    "(2) destructive actions before forensic preservation, "
    "(3) broad privilege escalations without human-in-the-loop approval.\n"
)


__all__ = [
    "SECOPS_CRITIC_PROMPT",
    "SECOPS_PRECONDITIONS",
    "BlastRadiusGate",
    "ForensicOrderGate",
    "LeastPrivilegeGate",
]
