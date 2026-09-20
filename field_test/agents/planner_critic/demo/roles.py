"""Demo roles (F-86, D11 §4): deterministic planner/critic for the corpus goals.

The sample corpus carries *seeded flaws* (documented in each ``_seeded_flaw``
field). These two roles make the demo hermetic: they implement the
planner/critic role protocols with no LLM so the real loop, re-gate, and
replan code runs deterministically against the corpus. They are the *only*
new logic in M7 — everything else is the production engine.

* :class:`ScriptedPlanner` — ``decompose`` returns the flawed revision 1 for
  a corpus goal; ``revise`` returns the corrected revision 2 (idempotent).
* :class:`ScriptedCritic` — flags revision 1 with the seeded finding and
  returns an empty finding list from revision 2 onward.

A scenario (:class:`_Scenario`) binds one corpus goal to its flawed/fixed
task graphs and the seeded finding metadata. Unknown goals fail closed with
:class:`~planner_critic.types.PlanningError` (D11 §8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..reason_codes import (
    LLM_FEASIBILITY,
    LLM_MISSING_STEPS,
    LLM_RISK,
    LLM_UNSAFE_SEQUENCING,
    LLM_UNVERIFIED_DEPENDENCIES,
    LLM_WEAK_ROLLBACK,
    ReasonCode,
)
from ..roles import CriticRole, PlannerRole
from ..schema.goal import Goal
from ..schema.plan import (
    Dependency,
    EnvProbe,
    PlanVersion,
    Precondition,
    RollbackStep,
    Task,
    VerificationStep,
)
from ..types import Finding, HeuristicFamily, PlanningError, Severity
from . import DEMO_WINDOW_EXPECTED, DEMO_WINDOW_VAR

#: heuristics that share a name with their corpus-vs-critic unit.
_FAMILY_REASON: dict[HeuristicFamily, ReasonCode] = {
    HeuristicFamily.FEASIBILITY: LLM_FEASIBILITY,
    HeuristicFamily.RISK: LLM_RISK,
    HeuristicFamily.MISSING_STEPS: LLM_MISSING_STEPS,
    HeuristicFamily.UNSAFE_SEQUENCING: LLM_UNSAFE_SEQUENCING,
    HeuristicFamily.UNVERIFIED_DEPENDENCIES: LLM_UNVERIFIED_DEPENDENCIES,
    HeuristicFamily.WEAK_ROLLBACK: LLM_WEAK_ROLLBACK,
}


@dataclass(frozen=True)
class _Scenario:
    """One corpus goal: its seeded finding and its flawed/fixed task graphs.

    Attributes:
        flaw_task_id: The task that carries the seeded flaw.
        family: The heuristic family the critic reports on revision 1.
        severity: The seeded finding's severity.
        message: The seeded finding's human-readable message.
        v1: Builds the flawed revision-1 plan for a goal id.
        v2: Builds the corrected revision-2 plan for a goal id.
    """

    flaw_task_id: str
    family: HeuristicFamily
    severity: Severity
    message: str
    v1: Callable[[str], PlanVersion]
    v2: Callable[[str], PlanVersion]

    @property
    def reason_code(self) -> ReasonCode:
        """The stable reason code for this scenario's heuristic family."""
        return _FAMILY_REASON[self.family]


# -- small builders ------------------------------------------------------------


def _plan(
    goal_id: str,
    *,
    version: int = 1,
    tasks: list[Task],
    deps: list[Dependency] | None = None,
) -> PlanVersion:
    """A plan revision for a corpus goal with a stable id/version."""
    return PlanVersion(
        id=f"plan-{goal_id}",
        goal_id=goal_id,
        version=version,
        tasks=tasks,
        dependencies=deps or [],
    )


def _task(
    task_id: str,
    description: str,
    *,
    risk: str = "medium",
    blast: str = "medium",
    verification: VerificationStep | None = None,
    rollback: RollbackStep | None = None,
    preconditions: list[Precondition] | None = None,
    satisfies: str | None = None,
) -> Task:
    """A task with explicit but convenient defaults (ids match descriptions)."""
    return Task.model_validate(
        {
            "id": task_id,
            "description": description,
            "action": "do",
            "target": task_id,
            "risk_class": risk,
            "blast_radius": blast,
            "verification": verification,
            "rollback": rollback,
            "preconditions": preconditions or [],
            "satisfies": satisfies,
        }
    )


def _verify(what: str, how: str, expected: str) -> VerificationStep:
    """A verification step for a task."""
    return VerificationStep(what=what, how=how, expected=expected)


def _rollback(trigger: str, action: str, guard: str = "") -> RollbackStep:
    """A rollback step, optionally with a safety guard.

    Includes default typed restoration fields so demo plans satisfy the
    v0.2.2 rollback_credible gate without emitting migration advisories.
    """
    return RollbackStep(
        trigger=trigger,
        action=action,
        safety_guard=guard,
        restores_state=["pre_rollback_state"],
        restoration_evidence="verify post-rollback state matches expected",
    )


def _precondition(
    description: str, fact: str, *, established_by: str | None = None, probed: bool = False
) -> Precondition:
    """A precondition, optionally grounded in a live env-var probe (D11 DD-M7-04)."""
    return Precondition(
        description=description,
        fact=fact,
        established_by=established_by,
        probe=EnvProbe(kind="env_var", query=DEMO_WINDOW_VAR, expected=DEMO_WINDOW_EXPECTED)
        if probed
        else None,
    )


def _dep(from_task: str, to_task: str) -> Dependency:
    """A hard ordering edge between two tasks."""
    return Dependency(from_task=from_task, to_task=to_task)


# -- canonical safety steps -----------------------------------------------------

_CUTOVER_VERIFY = _verify(
    what="post-cutover reads and writes",
    how="run the smoke-query suite against the new schema",
    expected="all smoke checks pass",
)
_CUTOVER_ROLLBACK = RollbackStep(
    trigger="any smoke check fails",
    action="flip traffic back to the legacy schema",
    safety_guard="pre-cutover snapshot is held",
    restores_state=["db_schema", "traffic_routing"],
    restoration_evidence="verify smoke suite re-passes on legacy schema",
)


# -- scenarios -----------------------------------------------------------------


def _migration() -> _Scenario:
    """missing_steps: the backup/verify step before cutover is absent in v1."""

    def v1(goal_id: str) -> PlanVersion:
        return _plan(
            goal_id,
            tasks=[
                _task(
                    "snapshot",
                    "Take a pre-cutover snapshot of the production database",
                    satisfies="criterion",
                ),
                _task("backfill", "Run the incremental schema backfill", satisfies="criterion"),
                _task(
                    "cutover",
                    "Switch traffic to the new schema",
                    risk="critical",
                    blast="critical",
                    verification=_CUTOVER_VERIFY,
                    rollback=_CUTOVER_ROLLBACK,
                    preconditions=[
                        _precondition(
                            "the maintenance window is open",
                            "maintenance window open",
                            probed=True,
                        )
                    ],
                    satisfies="criterion",
                ),
            ],
            deps=[_dep("snapshot", "backfill"), _dep("backfill", "cutover")],
        )

    def v2(goal_id: str) -> PlanVersion:
        return _plan(
            goal_id,
            version=2,
            tasks=[
                _task(
                    "snapshot",
                    "Take a pre-cutover snapshot of the production database",
                    satisfies="criterion",
                ),
                _task("backfill", "Run the incremental schema backfill", satisfies="criterion"),
                _task(
                    "verify",
                    "Verify schema compatibility before cutover",
                    verification=_verify(
                        "schema compatibility",
                        "run the version-compat query suite",
                        "compat suite passes",
                    ),
                    satisfies="criterion",
                ),
                _task(
                    "cutover",
                    "Switch traffic to the new schema",
                    risk="critical",
                    blast="critical",
                    verification=_CUTOVER_VERIFY,
                    rollback=_CUTOVER_ROLLBACK,
                    preconditions=[
                        _precondition(
                            "the maintenance window is open",
                            "maintenance window open",
                            probed=True,
                        )
                    ],
                    satisfies="criterion",
                ),
            ],
            deps=[
                _dep("snapshot", "backfill"),
                _dep("backfill", "verify"),
                _dep("verify", "cutover"),
            ],
        )

    return _Scenario(
        flaw_task_id="cutover",
        family=HeuristicFamily.MISSING_STEPS,
        severity=Severity.WARNING,
        message="no step backs up the schema or verifies compatibility before cutover",
        v1=v1,
        v2=v2,
    )


def _rollout() -> _Scenario:
    """unsafe_sequencing: the rollback window opens after the first 50%."""

    def v2_tasks() -> list[Task]:
        def phase(percent: str) -> Task:
            """One traffic-shift task for a percentage of the rollout."""
            return _task(
                f"phase-{percent}",
                f"Shift {percent}0% of traffic to the new fleet",
                risk="high",
                blast="high",
                verification=_verify(
                    f"phase-{percent} traffic", "check error budget", "budget holds"
                ),
                rollback=_rollback(
                    f"phase-{percent} fails",
                    "shift traffic back",
                    "rollback window is open",
                ),
                satisfies="criterion",
            )

        return [
            _task("create", "Create the new API fleet", satisfies="criterion"),
            _task(
                "rollback-window",
                "Open the global rollback window",
                risk="high",
                blast="high",
                verification=_verify("rollback path", "arm rollback switches", "armed"),
                rollback=_rollback("armed incorrectly", "disarm switches", "no traffic moved"),
                satisfies="criterion",
            ),
            phase("5"),
            phase("10"),
        ]

    def v1(goal_id: str) -> PlanVersion:
        tasks = [
            _task("create", "Create the new API fleet", satisfies="criterion"),
            _task(
                "phase-5",
                "Shift 50% of traffic to the new fleet",
                risk="high",
                blast="high",
                verification=_verify("phase-5 traffic", "check error budget", "budget holds"),
                rollback=_rollback("phase-5 fails", "shift traffic back", ""),
                satisfies="criterion",
            ),
            _task(
                "phase-10",
                "Shift the remaining 50% of traffic to the new fleet",
                risk="high",
                blast="high",
                verification=_verify("phase-10 traffic", "check error budget", "budget holds"),
                rollback=_rollback("phase-10 fails", "shift traffic back", ""),
                satisfies="criterion",
            ),
            _task(
                "rollback-window",
                "Open the global rollback window",
                risk="high",
                blast="high",
                verification=_verify("rollback path", "arm rollback switches", "armed"),
                rollback=_rollback("armed incorrectly", "disarm switches", "no traffic moved"),
                satisfies="criterion",
            ),
        ]
        return _plan(
            goal_id,
            tasks=tasks,
            deps=[
                _dep("create", "phase-5"),
                _dep("phase-5", "phase-10"),
                _dep("phase-10", "rollback-window"),
            ],
        )

    def v2(goal_id: str) -> PlanVersion:
        tasks = v2_tasks()
        return _plan(
            goal_id,
            version=2,
            tasks=tasks,
            deps=[
                _dep("create", "rollback-window"),
                _dep("rollback-window", "phase-5"),
                _dep("phase-5", "phase-10"),
            ],
        )

    return _Scenario(
        flaw_task_id="rollback-window",
        family=HeuristicFamily.UNSAFE_SEQUENCING,
        severity=Severity.WARNING,
        message="rollback window opens only after half the rollout steps",
        v1=v1,
        v2=v2,
    )


def _refactor() -> _Scenario:
    """unverified_dependencies: the booked window is never probe-verified."""

    def v1(goal_id: str) -> PlanVersion:
        return _plan(
            goal_id,
            tasks=[
                _task(
                    "book",
                    "Book the maintenance window with the platform team",
                    satisfies="criterion",
                ),
                _task("backup", "Back up the current routing state", satisfies="criterion"),
                _task(
                    "swap",
                    "Swap checkout onto the new payments API",
                    risk="critical",
                    blast="critical",
                    verification=_verify(
                        "checkout traffic", "run the checkout smoke suite", "smokes pass"
                    ),
                    rollback=_rollback("smokes fail", "route back to the old API", "backup held"),
                    preconditions=[
                        _precondition(
                            "the maintenance window is booked",
                            "maintenance window booked",
                            established_by="book",
                        )
                    ],
                    satisfies="criterion",
                ),
            ],
            deps=[_dep("book", "swap"), _dep("backup", "swap")],
        )

    def v2(goal_id: str) -> PlanVersion:
        return _plan(
            goal_id,
            version=2,
            tasks=[
                _task(
                    "book",
                    "Book the maintenance window with the platform team",
                    satisfies="criterion",
                ),
                _task("backup", "Back up the current routing state", satisfies="criterion"),
                _task(
                    "swap",
                    "Swap checkout onto the new payments API",
                    risk="critical",
                    blast="critical",
                    verification=_verify(
                        "checkout traffic", "run the checkout smoke suite", "smokes pass"
                    ),
                    rollback=_rollback("smokes fail", "route back to the old API", "backup held"),
                    preconditions=[
                        _precondition(
                            "the maintenance window is booked",
                            "maintenance window booked",
                            established_by="book",
                            probed=True,
                        )
                    ],
                    satisfies="criterion",
                ),
            ],
            deps=[_dep("book", "swap"), _dep("backup", "swap")],
        )

    return _Scenario(
        flaw_task_id="swap",
        family=HeuristicFamily.UNVERIFIED_DEPENDENCIES,
        severity=Severity.WARNING,
        message="the swap depends on a booked window that is never probe-verified",
        v1=v1,
        v2=v2,
    )


def _incident() -> _Scenario:
    """weak_rollback: the irreversible write has no guard and no mitigation check."""

    def v1(goal_id: str) -> PlanVersion:
        return _plan(
            goal_id,
            tasks=[
                _task("diagnose", "Diagnose the payment-processing failure", satisfies="criterion"),
                _task(
                    "remediate",
                    "Apply the irreversible remediation write",
                    risk="critical",
                    blast="critical",
                    verification=_verify("write applied", "re-read the ledger row", "matches"),
                    rollback=_rollback("remedy fails", "revert the write", ""),
                    satisfies="criterion",
                ),
            ],
            deps=[_dep("diagnose", "remediate")],
        )

    def v2(goal_id: str) -> PlanVersion:
        return _plan(
            goal_id,
            version=2,
            tasks=[
                _task("diagnose", "Diagnose the payment-processing failure", satisfies="criterion"),
                _task(
                    "remediate",
                    "Apply the irreversible remediation write",
                    risk="critical",
                    blast="critical",
                    verification=_verify("write applied", "re-read the ledger row", "matches"),
                    rollback=_rollback(
                        "remedy fails",
                        "revert the write",
                        "no dependent actors are mid-write and the snapshot is held",
                    ),
                    satisfies="criterion",
                ),
                _task(
                    "verify-mitigation",
                    "Verify the incident is mitigated",
                    verification=_verify("mitigation", "run the payment health checks", "healthy"),
                    satisfies="criterion",
                ),
            ],
            deps=[_dep("diagnose", "remediate"), _dep("remediate", "verify-mitigation")],
        )

    return _Scenario(
        flaw_task_id="remediate",
        family=HeuristicFamily.WEAK_ROLLBACK,
        severity=Severity.WARNING,
        message="the irreversible write's rollback has no safety guard",
        v1=v1,
        v2=v2,
    )


def _adversarial() -> _Scenario:
    """feasibility/structural: a deterministic gate rejects v1 (F-73, inject-safe)."""

    def v1(goal_id: str) -> PlanVersion:
        return _plan(
            goal_id,
            tasks=[
                _task(
                    "cutover",
                    "Cut the payment database over to the new storage engine",
                    risk="critical",
                    blast="critical",
                    satisfies="criterion",
                )
            ],
        )

    def v2(goal_id: str) -> PlanVersion:
        return _plan(
            goal_id,
            version=2,
            tasks=[
                _task(
                    "cutover",
                    "Cut the payment database over to the new storage engine",
                    risk="critical",
                    blast="critical",
                    verification=_verify(
                        "storage compatibility", "run the compatibility checks", "pass"
                    ),
                    rollback=_rollback("checks fail", "switch back", "snapshot held"),
                    satisfies="criterion",
                )
            ],
        )

    return _Scenario(
        flaw_task_id="cutover",
        family=HeuristicFamily.FEASIBILITY,
        severity=Severity.BLOCKER,
        message="critical-risk cutover has no verification and no rollback",
        v1=v1,
        v2=v2,
    )


_SCENARIOS: dict[str, _Scenario] = {
    "demo-migration": _migration(),
    "demo-rollout": _rollout(),
    "demo-refactor": _refactor(),
    "demo-incident": _incident(),
    "demo-adversarial": _adversarial(),
}


def _scenario_for(goal_id: str) -> _Scenario:
    """Look up a scenario by goal id; fail closed when unknown (D11 §8)."""
    scenario = _SCENARIOS.get(goal_id)
    if scenario is None:
        raise PlanningError(f"no demo scenario for goal {goal_id!r}")
    return scenario


class ScriptedPlanner(PlannerRole):
    """Deterministic planner for the corpus (no LLM, pure functions)."""

    def decompose(self, goal: Goal) -> PlanVersion:
        """Return the flawed revision 1 for the goal's scenario."""
        return _scenario_for(goal.id).v1(goal.id)

    def revise(self, plan: PlanVersion, findings: list[Finding]) -> PlanVersion:
        """Return the corrected revision 2 (idempotent per goal)."""
        return _scenario_for(plan.goal_id).v2(plan.goal_id)


class ScriptedCritic(CriticRole):
    """Deterministic critic: flags revision 1, clears everything after."""

    def audit(self, plan: PlanVersion, findings: list[Finding]) -> list[Finding]:
        """Return the seeded finding on revision 1, otherwise an empty list."""
        if plan.version != 1:
            return []
        scenario = _scenario_for(plan.goal_id)
        return [
            Finding(
                id=f"demo:{plan.goal_id}:{scenario.flaw_task_id}",
                task_id=scenario.flaw_task_id,
                version=plan.version,
                heuristic_family=scenario.family,
                severity=scenario.severity,
                reason_code=scenario.reason_code,
                message=scenario.message,
                suggested_fix=f"revise to address the seeded '{scenario.family}' flaw",
            )
        ]


__all__ = ["ScriptedCritic", "ScriptedPlanner"]
