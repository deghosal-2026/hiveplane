"""Deterministic-gate security regression corpus (M5, #126).

Derives plan artifacts from SWE-bench security instances: one correct skeleton
plus ≥5 flawed variants per instance, each labeled with the expected gate and
reason code. Hermetic (no LLM), designed for CI regression assertions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..corpus.types import SecurityInstance
from ..reason_codes import (
    DEPENDENCY_CYCLE,
    MISSING_ROLLBACK,
    MISSING_VERIFICATION,
    UNSAFE_ORDERING,
    UNVERIFIED_PRECONDITION,
    ReasonCode,
)
from ..schema.plan import Dependency, DependencyKind, PlanVersion, Task


def _make_task(
    tid: str,
    action: str = "fix",
    target: str | None = None,
    risk_class: str = "high",
    blast_radius: str = "high",
    has_verification: bool = True,
    has_rollback: bool = True,
    preconditions: list[dict[str, object]] | None = None,
) -> Task:
    """Build a task with deterministic structure for gate regression."""
    task_data: dict[str, Any] = {
        "id": tid,
        "description": f"{action} {target or tid}",
        "action": action,
        "target": target or tid,
        "risk_class": risk_class,
        "blast_radius": blast_radius,
    }
    if has_verification:
        task_data["verification"] = {
            "what": "security fix applied correctly",
            "how": "manual_review",
            "expected": "pass",
        }
    if has_rollback:
        task_data["rollback"] = {
            "trigger": "verification_fails",
            "action": "revert",
            "safety_guard": "backup_confirmed",
        }
    if preconditions:
        task_data["preconditions"] = preconditions
    return Task.model_validate(task_data)


def _dep(from_task: str, to_task: str) -> Dependency:
    return Dependency.model_validate(
        {"from_task": from_task, "to_task": to_task, "kind": DependencyKind.HARD}
    )


@dataclass
class PlanArtifact:
    """A correct plan skeleton and its flawed variants for one instance.

    Attributes:
        instance_id: The source corpus instance ID.
        correct: A plan skeleton that passes all deterministic gates.
        variants: Labeled flawed variants, each with expected blocker
            reason code.
        variant_labels: Description of what each variant is testing.
    """

    instance_id: str
    correct: PlanVersion
    variants: list[PlanVersion] = field(default_factory=list)
    variant_labels: list[str] = field(default_factory=list)
    variant_expected: list[ReasonCode] = field(default_factory=list)

    @property
    def variant_count(self) -> int:
        return len(self.variants)


def generate_artifact(instance: SecurityInstance) -> PlanArtifact:
    """Generate a correct plan + flawed variants from a security instance.

    The correct skeleton models a security fix with two steps:
    1. validate/prepare the fix
    2. apply the fix
    Both steps carry verification and rollback; ordering is correct.

    Flawed variants:
    1. Missing verification (drop verification from step 2)
    2. Missing rollback (drop rollback from step 2)
    3. Unsafe ordering (swap validate and apply)
    4. Dependency cycle (add back-edge)
    5. Missing precondition (add precondition without establishing it)
    """
    prefix = instance.instance_id.lower().replace("-", "_")

    correct = PlanVersion.model_validate(
        {
            "id": f"{prefix}-correct",
            "goal_id": instance.instance_id,
            "version": 1,
            "tasks": [
                _make_task(
                    f"{prefix}_validate",
                    action="validate",
                    risk_class="low",
                    blast_radius="low",
                    has_verification=True,
                    has_rollback=False,
                ),
                _make_task(
                    f"{prefix}_apply_fix",
                    action="fix",
                    target=instance.vulnerability_class.replace("_", "-"),
                    risk_class="high",
                    blast_radius="high",
                    has_verification=True,
                    has_rollback=True,
                ),
            ],
            "dependencies": [
                _dep(f"{prefix}_validate", f"{prefix}_apply_fix"),
            ],
        }
    )

    variants: list[PlanVersion] = []
    labels: list[str] = []
    expected: list[ReasonCode] = []

    # Variant 1: Missing verification on high-blast-radius step
    variants.append(
        PlanVersion.model_validate(
            {
                "id": f"{prefix}-no-verify",
                "goal_id": instance.instance_id,
                "version": 1,
                "tasks": [
                    _make_task(
                        f"{prefix}_validate",
                        risk_class="low",
                        blast_radius="low",
                        has_verification=False,
                    ),
                    _make_task(
                        f"{prefix}_apply_fix",
                        risk_class="high",
                        blast_radius="high",
                        has_verification=False,
                        has_rollback=True,
                    ),
                ],
                "dependencies": [
                    _dep(f"{prefix}_validate", f"{prefix}_apply_fix"),
                ],
            }
        )
    )
    labels.append("drop verification on high-blast-radius step")
    expected.append(MISSING_VERIFICATION)

    # Variant 2: Missing rollback on high-blast-radius step
    variants.append(
        PlanVersion.model_validate(
            {
                "id": f"{prefix}-no-rollback",
                "goal_id": instance.instance_id,
                "version": 1,
                "tasks": [
                    _make_task(
                        f"{prefix}_validate",
                        risk_class="low",
                        blast_radius="low",
                    ),
                    _make_task(
                        f"{prefix}_apply_fix",
                        risk_class="high",
                        blast_radius="high",
                        has_verification=True,
                        has_rollback=False,
                    ),
                ],
                "dependencies": [
                    _dep(f"{prefix}_validate", f"{prefix}_apply_fix"),
                ],
            }
        )
    )
    labels.append("drop rollback on high-blast-radius step")
    expected.append(MISSING_ROLLBACK)

    # Variant 3: Unsafe ordering (apply before validate)
    variants.append(
        PlanVersion.model_validate(
            {
                "id": f"{prefix}-bad-order",
                "goal_id": instance.instance_id,
                "version": 1,
                "tasks": [
                    _make_task(
                        f"{prefix}_apply_fix",
                        risk_class="high",
                        blast_radius="high",
                    ),
                    _make_task(
                        f"{prefix}_validate",
                        risk_class="low",
                        blast_radius="low",
                    ),
                ],
                "dependencies": [
                    _dep(f"{prefix}_validate", f"{prefix}_apply_fix"),
                ],
            }
        )
    )
    labels.append("unsafe ordering: apply before validate")
    expected.append(UNSAFE_ORDERING)

    # Variant 4: Dependency cycle
    variants.append(
        PlanVersion.model_validate(
            {
                "id": f"{prefix}-cycle",
                "goal_id": instance.instance_id,
                "version": 1,
                "tasks": [
                    _make_task(f"{prefix}_a", risk_class="low", blast_radius="low"),
                    _make_task(f"{prefix}_b", risk_class="low", blast_radius="low"),
                ],
                "dependencies": [
                    _dep(f"{prefix}_a", f"{prefix}_b"),
                    _dep(f"{prefix}_b", f"{prefix}_a"),
                ],
            }
        )
    )
    labels.append("dependency cycle between two tasks")
    expected.append(DEPENDENCY_CYCLE)

    # Variant 5: Precondition references unestablished fact
    variants.append(
        PlanVersion.model_validate(
            {
                "id": f"{prefix}-unverified-precond",
                "goal_id": instance.instance_id,
                "version": 1,
                "tasks": [
                    _make_task(
                        f"{prefix}_apply_fix",
                        risk_class="low",
                        blast_radius="low",
                        preconditions=[
                            {
                                "description": (
                                    "Security review must be approved before applying fix"
                                ),
                                "fact": "security_review_approved",
                                "established_by": None,
                            }
                        ],
                    ),
                ],
            }
        )
    )
    labels.append("precondition references fact not established by earlier step")
    expected.append(UNVERIFIED_PRECONDITION)

    return PlanArtifact(
        instance_id=instance.instance_id,
        correct=correct,
        variants=variants,
        variant_labels=labels,
        variant_expected=expected,
    )


__all__ = [
    "PlanArtifact",
    "generate_artifact",
]
