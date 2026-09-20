"""Deterministic auto-fix passes (M2) — code, not model output.

Runs after the deterministic gates and before the LLM critic, the same
position in the loop pseudocode where a gate blocker triggers a revision.
These passes fix structurally-trivial problems without an LLM round-trip,
preserving the §2.4 injection-immunity property (nothing here consults a
model) while lowering the median revisions-to-approval target (§7.1).

* :func:`apply_ordering_auto_repair` — re-orders tasks via a deterministic
  topological sort when the *only* gate blockers are ordering-only
  violations (``unsafe_ordering`` with an acyclic graph). Emits an
  ``auto_repaired_ordering`` info finding so the repair is visible in the
  trace. Cycles, unsafe parallelization, and any other blocker family are
  never auto-repaired — they fall through to the LLM critic.
* :func:`apply_precondition_closer` — matches ``unverified_precondition``
  findings against a seed template library and auto-injects the missing
  step before the task that depends on it. Emits an
  ``auto_closed_precondition`` info finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..reason_codes import (
    AUTO_CLOSED_PRECONDITION,
    AUTO_REPAIRED_ORDERING,
    DEPENDENCY_CYCLE,
    UNSAFE_ORDERING,
    UNVERIFIED_PRECONDITION,
)
from ..schema.plan import DependencyKind, PlanVersion, Task
from ..types import Finding, Severity

# ── Precondition-template library ─────────────────────────────────────────


@dataclass(frozen=True)
class PreconditionTemplate:
    """A seed template for auto-closing a ``unverified_precondition`` finding.

    Attributes:
        name: Stable template identifier (used as the injected task id).
        pattern: Case-insensitive substring matched against the finding message
            (or the precondition fact key).
        task_fields: Keyword arguments passed to ``Task.model_validate`` to
            build the synthesised step.
    """

    name: str
    pattern: str
    task_fields: dict[str, object] = field(default_factory=dict)


SEED_TEMPLATES: list[PreconditionTemplate] = [
    PreconditionTemplate(
        name="book-outage-window",
        pattern="outage",
        task_fields={
            "id": "book-outage-window",
            "description": "Book maintenance outage window with the platform team",
            "action": "book",
            "target": "outage_window",
            "risk_class": "low",
        },
    ),
    PreconditionTemplate(
        name="run-schema-compat-check",
        pattern="schema",
        task_fields={
            "id": "run-schema-compat-check",
            "description": "Run schema compatibility check against the target database",
            "action": "check",
            "target": "schema_compatibility",
            "risk_class": "low",
        },
    ),
    PreconditionTemplate(
        name="verify-credential-rotation",
        pattern="credential",
        task_fields={
            "id": "verify-credential-rotation",
            "description": "Verify credential rotation is complete before proceeding",
            "action": "verify",
            "target": "credential_rotation",
            "risk_class": "low",
        },
    ),
    PreconditionTemplate(
        name="snapshot-before-migration",
        pattern="snapshot",
        task_fields={
            "id": "snapshot-before-migration",
            "description": "Create a database snapshot before applying the migration",
            "action": "snapshot",
            "target": "database_snapshot",
            "risk_class": "low",
        },
    ),
    PreconditionTemplate(
        name="check-capacity-headroom",
        pattern="capacity",
        task_fields={
            "id": "check-capacity-headroom",
            "description": "Check capacity headroom in the target cluster",
            "action": "check",
            "target": "capacity_headroom",
            "risk_class": "low",
        },
    ),
]


# ── Topological auto-repair (#130) ─────────────────────────────────────────


def topological_reorder(plan: PlanVersion) -> PlanVersion | None:
    """Return ``plan`` with tasks re-ordered to honor hard dependencies.

    Uses a deterministic topological sort that breaks ties by original list
    index, so independent tasks keep their relative order (minimal
    disruption). Returns ``None`` when there is nothing to re-order (the plan
    already satisfies every hard edge) or when no valid order exists (a
    dependency cycle).

    Args:
        plan: The plan whose task list may be out of order.

    Returns:
        A new :class:`PlanVersion` with the re-ordered task list, or ``None``
        when no re-ordering is possible/needed.
    """
    hard_edges = [
        (dep.from_task, dep.to_task) for dep in plan.dependencies if dep.kind is DependencyKind.HARD
    ]
    if not hard_edges:
        return None

    by_index = {task.id: index for index, task in enumerate(plan.tasks)}
    id_to_task = {task.id: task for task in plan.tasks}

    #: Hard predecessors of each task id.
    predecessors: dict[str, set[str]] = {task.id: set() for task in plan.tasks}
    for src, dst in hard_edges:
        predecessors[dst].add(src)

    placed: list[str] = []
    remaining: set[str] = set(id_to_task)
    while remaining:
        available = [tid for tid in remaining if predecessors[tid] <= set(placed)]
        if not available:
            # Every remaining node still has an unplaced predecessor: a cycle.
            return None
        pick = min(available, key=lambda tid: by_index[tid])
        placed.append(pick)
        remaining.remove(pick)

    reordered = [id_to_task[tid] for tid in placed]
    if reordered == plan.tasks:
        return None
    return plan.model_copy(update={"tasks": reordered})


def apply_ordering_auto_repair(
    plan: PlanVersion, findings: list[Finding]
) -> tuple[PlanVersion | None, list[Finding]]:
    """Auto-repair ordering-only gate violations, if any.

    Fires *only* when every blocker is an ``unsafe_ordering`` finding and the
    dependency graph is acyclic. Any other blocker (missing verification /
    rollback, cycle, unsafe parallelization) or a mixed blocker set disables
    the pass so the LLM critic handles the findings as before.

    Args:
        plan: The plan whose gate findings include ordering violations.
        findings: The gate findings collected for this revision.

    Returns:
        ``(repaired_plan, trace_findings)``. ``repaired_plan`` is ``None``
        when no repair applies; otherwise it is the re-ordered plan and
        ``trace_findings`` carries a single ``auto_repaired_ordering`` info
        finding for the trace.
    """
    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    ordering_only = bool(blockers) and all(f.reason_code == UNSAFE_ORDERING for f in blockers)
    has_cycle = any(f.reason_code == DEPENDENCY_CYCLE for f in findings)
    if not ordering_only or has_cycle:
        return None, []

    repaired = topological_reorder(plan)
    if repaired is None:
        return None, []

    trace = Finding(
        id=f"auto_repair:{plan.id}:{plan.version}:ordering",
        version=plan.version,
        severity=Severity.INFO,
        reason_code=AUTO_REPAIRED_ORDERING,
        message="auto-repaired task ordering to satisfy hard-dependency precedences",
    )
    return repaired, [trace]


# ── Precondition closer (#131) ─────────────────────────────────────────────


def _match_template(
    finding: Finding, templates: list[PreconditionTemplate]
) -> PreconditionTemplate | None:
    """Return the first template whose pattern matches the finding.

    The pattern is a case-insensitive substring of the finding message.
    """
    msg_lower = (finding.message or "").lower()
    for tmpl in templates:
        if tmpl.pattern.lower() in msg_lower:
            return tmpl
    return None


def apply_precondition_closer(
    plan: PlanVersion,
    findings: list[Finding],
    templates: list[PreconditionTemplate] | None = None,
) -> tuple[PlanVersion | None, list[Finding]]:
    """Auto-inject a missing step for a template-matched precondition gap.

    Iterates over the findings; on the first ``unverified_precondition``
    finding whose message matches a template's pattern, synthesises the
    template's task and inserts it *before* the failing task. The plan is
    returned along with an ``auto_closed_precondition`` info finding.

    Args:
        plan: The plan whose gate findings include precondition gaps.
        findings: The gate findings collected for this revision.
        templates: Template library; falls back to :data:`SEED_TEMPLATES`.

    Returns:
        ``(patched_plan, trace_findings)``. ``patched_plan`` is ``None`` when
        no template matched; otherwise it is the plan with the injected task
        and an info finding recording the close.
    """
    if templates is None:
        templates = SEED_TEMPLATES

    for f in findings:
        if f.reason_code != UNVERIFIED_PRECONDITION:
            continue
        template = _match_template(f, templates)
        if template is None:
            continue

        new_task = Task.model_validate(template.task_fields)
        target_id = f.task_id
        if target_id is None:
            continue

        # Skip if a task with this ID already exists (prevents duplicates)
        if any(t.id == new_task.id for t in plan.tasks):
            continue

        new_tasks: list[Task] = []
        injected = False
        for task in plan.tasks:
            if task.id == target_id and not injected:
                new_tasks.append(new_task)
                injected = True
            new_tasks.append(task)

        patched = plan.model_copy(update={"tasks": new_tasks})

        trace = Finding(
            id=f"auto_close:{plan.id}:{plan.version}:{template.name}",
            version=plan.version,
            severity=Severity.INFO,
            reason_code=AUTO_CLOSED_PRECONDITION,
            message=(
                f"auto-closed precondition gap via template {template.name!r} "
                f"— injected task {template.name!r} before {target_id!r}"
            ),
        )
        return patched, [trace]

    return None, []


__all__ = [
    "SEED_TEMPLATES",
    "PreconditionTemplate",
    "apply_ordering_auto_repair",
    "apply_precondition_closer",
    "topological_reorder",
]
