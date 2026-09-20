"""rollback_credible gate — presence is not credibility (#216).

``rollback_present`` fires when a high-risk task has *no* rollback section.
It cannot see a rollback that exists but cannot do its job. This gate adds
the credibility half, deriving every check from existing schema structure
(no new schema fields — patch invariant) and the #160 action-inversion
registry:

* **Unreachable** (:data:`~planner_critic.reason_codes.ROLLBACK_UNREACHABLE`)
  — the forward action has no automated inverse (publish / destroy /
  commit per the registry), so the named rollback path cannot execute.
* **Self-dependent** (:data:`...ROLLBACK_SELF_DEPENDENT`) — the task's own
  preconditions claim establishment by the task itself. Literal ``T→T``
  dependency edges are rejected by plan validation, so the derivable form
  of a circular basis is a self-referential precondition.
* **Inconsistent-state** (:data:`...ROLLBACK_INCONSISTENT_STATE`) — a later
  task's precondition fact is established by this task, but that later task
  has neither verification nor rollback: restoring pre-write state silently
  invalidates its basis with nothing to re-check or compensate.
* **Post-consumed recovery** (:data:`...ROLLBACK_POST_CONSUMED`) — a
  hard-dependency consumer runs after the producer with neither verification
  nor rollback. If the producer ever rolls back, the state that consumer
  already used is erased with no re-sync — the dual-write window from the
  Part 3 community thread.

Consumers that verify are exempt from the last two patterns: re-verification
re-establishes validity after any restore. All findings are gate-layer
BLOCKERs — the LLM critic never votes on them.
"""

from __future__ import annotations

from ..reason_codes import (
    ROLLBACK_INCONSISTENT_STATE,
    ROLLBACK_POST_CONSUMED,
    ROLLBACK_SELF_DEPENDENT,
    ROLLBACK_STATE_UNDECLARED,
    ROLLBACK_UNREACHABLE,
)
from ..rollback_synth import _NON_REVERSIBLE_ACTIONS
from ..schema.plan import PlanVersion, Task
from ..types import Finding, Severity
from .base import BaseGate


def _is_high_blast(task: Task) -> bool:
    """True when the task is high-risk by risk_class or blast_radius."""
    return task.risk_class.is_high_risk or task.blast_radius in ("high", "critical")


def _bare_consumer(task: Task) -> bool:
    """A consumer with neither verification nor rollback cannot survive a restore."""
    return task.verification is None and task.rollback is None


def _has_typed_restoration(task: Task) -> bool:
    """True when the task declares typed restoration fields.

    ``None`` means legacy prose-only (no typed fields) — not typed.
    ``[]`` means explicitly empty — also not typed.
    Non-empty list or restoration_evidence — typed.
    """
    rb = task.rollback
    if rb is None:
        return False
    has_state = rb.restores_state is not None and len(rb.restores_state) > 0
    has_evidence = rb.restoration_evidence is not None
    return has_state or has_evidence


class Gate(BaseGate):
    """Flags high-risk rollbacks that exist but cannot credibly undo."""

    name = "rollback_credible"

    def run(self, plan: PlanVersion) -> list[Finding]:
        """Audit each high-risk task's claimed rollback for credibility.

        Args:
            plan: The typed plan to audit.

        Returns:
            Findings in gate-stable order. Empty when every claimed rollback
            is credible.
        """
        id_to_index = {task.id: index for index, task in enumerate(plan.tasks)}
        by_id = {task.id: task for task in plan.tasks}
        consumers_of: dict[str, list[str]] = {}
        for dep in plan.dependencies:
            if dep.kind.value == "hard" and dep.to_task in id_to_index:
                consumers_of.setdefault(dep.from_task, []).append(dep.to_task)

        findings: list[Finding] = []
        for producer in plan.tasks:
            if not _is_high_blast(producer):
                continue
            if not _is_reachable(producer):
                findings.append(
                    self._finding(
                        plan,
                        producer.id,
                        ROLLBACK_UNREACHABLE,
                        f"action {producer.action!r} has no automated inverse; "
                        f"the named rollback cannot execute",
                        "Replace the rollback with a snapshot-restore step or "
                        "re-stage the change behind an invertible action",
                    )
                )
            self_dep_n = 0
            for pre in producer.preconditions:
                if pre.established_by == producer.id:
                    findings.append(
                        self._finding(
                            plan,
                            producer.id,
                            ROLLBACK_SELF_DEPENDENT,
                            f"precondition fact {pre.fact!r} claims establishment "
                            f"by the task itself",
                            "Establish the fact from an earlier task or an env probe",
                            index=self_dep_n,
                        )
                    )
                    self_dep_n += 1

            if producer.rollback is None:
                continue

            # Typed restoration contract check (v0.2.2) — advisory, not blocker
            if not _has_typed_restoration(producer):
                findings.append(
                    Finding(
                        id=f"rollback_credible:{plan.id}:{plan.version}:{producer.id}:{ROLLBACK_STATE_UNDECLARED}",
                        task_id=producer.id,
                        version=plan.version,
                        severity=Severity.WARNING,
                        reason_code=ROLLBACK_STATE_UNDECLARED,
                        message=(
                            f"high-risk task {producer.id!r} has rollback but does not declare "
                            f"what state it restores (restores_state) or how restoration is "
                            f"verified (restoration_evidence)"
                        ),
                        suggested_fix=(
                            "Add restores_state and restoration_evidence "
                            "fields to the rollback step"
                        ),
                    )
                )
            else:
                # Contradiction check: restores_state facts must not be self-referential
                restores_set = set(producer.rollback.restores_state or [])
                for pre in producer.preconditions:
                    if pre.established_by == producer.id and pre.fact in restores_set:
                        findings.append(
                            self._finding(
                                plan,
                                producer.id,
                                ROLLBACK_SELF_DEPENDENT,
                                f"restores_state fact {pre.fact!r} is a precondition "
                                f"claimed by the task itself — contradictory declaration",
                                "Remove the self-referential fact from restores_state or "
                                "establish it from an earlier task",
                                index=self_dep_n,
                            )
                        )
                        self_dep_n += 1

            for code, consumer_id in _state_risks(plan, producer, by_id, id_to_index, consumers_of):
                if code == ROLLBACK_POST_CONSUMED:
                    message = (
                        f"task {consumer_id!r} hard-depends on this task with neither "
                        f"verification nor rollback; if this task rolls back, state "
                        f"{consumer_id!r} already consumed is erased with no re-sync"
                    )
                    fix = (
                        f"add verification or a rollback to {consumer_id!r}, or move it "
                        f"ahead of this task"
                    )
                else:  # ROLLBACK_INCONSISTENT_STATE
                    message = (
                        f"task {consumer_id!r} bases a precondition fact on this task yet "
                        f"has neither verification nor rollback; restoring pre-write "
                        f"state silently invalidates its basis"
                    )
                    fix = f"add verification or a rollback to {consumer_id!r}"
                findings.append(self._finding(plan, consumer_id, code, message, fix))
        return findings

    def _finding(
        self,
        plan: PlanVersion,
        task_id: str,
        reason_code: str,
        message: str,
        suggested_fix: str,
        *,
        index: int = 0,
    ) -> Finding:
        """Build one credibility blocker.

        ``index`` disambiguates findings that share a (task, reason_code) key
        (#234) — e.g. two self-referential preconditions on one task — so
        downstream ``{f.id: f}`` merges cannot collapse distinct defects. The
        suffix is omitted for the first finding, keeping every existing
        single-finding id string unchanged.
        """
        suffix = f":{index}" if index else ""
        return Finding(
            id=f"rollback_credible:{plan.id}:{plan.version}:{task_id}:{reason_code}{suffix}",
            task_id=task_id,
            version=plan.version,
            severity=Severity.BLOCKER,
            reason_code=reason_code,  # type: ignore[arg-type]
            message=message,
            suggested_fix=suggested_fix,
        )


def _is_reachable(task: Task) -> bool:
    """False when the action provably has no automated inverse."""
    if task.rollback is None:
        return True
    return task.action not in _NON_REVERSIBLE_ACTIONS


def _state_risks(
    plan: PlanVersion,
    producer: Task,
    by_id: dict[str, Task],
    id_to_index: dict[str, int],
    consumers_of: dict[str, list[str]],
) -> list[tuple[str, str]]:
    """Find later tasks whose basis does not survive this producer's rollback.

    Tasks with typed restoration evidence (restoration_evidence present)
    or typed restoration contracts (restores_state declared) on the producer
    are exempt from the post-consumed and inconsistent-state patterns.

    Returns ``(reason_code, offending_task_id)`` pairs: one per bare consumer
    whose precondition basis or declared consumption would be invalidated by
    restoring the producer's pre-write state.
    """
    risks: list[tuple[str, str]] = []

    # If the producer declares typed restoration, its consumers are exempt
    # from post-consumed / inconsistent-state risks
    if _has_typed_restoration(producer):
        return risks

    producer_index = id_to_index[producer.id]
    seen: set[str] = set()
    for dep_to in consumers_of.get(producer.id, []):
        if dep_to == producer.id or dep_to not in by_id:
            continue
        consumer = by_id[dep_to]
        if not _bare_consumer(consumer):
            continue
        risks.append((ROLLBACK_POST_CONSUMED, dep_to))
        seen.add(dep_to)

    for later_id, index in id_to_index.items():
        if index <= producer_index or later_id in seen:
            continue
        later = by_id[later_id]
        if not _bare_consumer(later):
            continue
        for pre in later.preconditions:
            if pre.established_by == producer.id:
                risks.append((ROLLBACK_INCONSISTENT_STATE, later_id))
                break
    return risks


__all__ = ["Gate"]
