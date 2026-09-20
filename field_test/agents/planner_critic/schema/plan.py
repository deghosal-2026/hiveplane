"""The typed plan schema (F-02, F-15): the versioned planning artifact.

A :class:`PlanVersion` is the immutable currency of the engine. Every
revision the planner produces is stored as a new, immutable ``PlanVersion``
linked to its parent, so the full draft→critique→revise history is
diff-able and replay-able.

Per PRD §2.8 the schema carries explicit parallel/branch semantics beyond
plain dependency edges: tasks declare a nullable ``parallel_group`` (tasks
in the same group run concurrently) and optional :class:`Branch` objects
express fan-in/fan-out shape with a join mode.

Structural invariants enforced at construction time:

* task ids are unique,
* every dependency endpoint and branch reference resolves to a task,
* a ``parallel_group`` may not contain a task id (tasks, not groups, are
  members), and a hard dependency may not exist *between* two tasks of the
  same ``parallel_group`` (they run concurrently — a contradiction).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLAN_SCHEMA_VERSION = "0.1.0"


class DependencyKind(StrEnum):
    """How a dependency constrains ordering: hard (must) or soft (should)."""

    HARD = "hard"
    SOFT = "soft"


class JoinMode(StrEnum):
    """How a fan-in branch considers its incoming tasks done."""

    ALL = "all"
    ANY = "any"
    QUORUM = "quorum"


class BranchKind(StrEnum):
    """Whether a branch fans out into parallel tasks or fans them back in."""

    FAN_OUT = "fan_out"
    FAN_IN = "fan_in"


class RiskClass(StrEnum):
    """A task's risk class; drives verification/rollback expectations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def is_high_risk(self) -> bool:
        """True for HIGH/CRITICAL — the deterministic gates require safety steps."""
        return self in (RiskClass.HIGH, RiskClass.CRITICAL)


class VerificationStep(BaseModel):
    """What to check, how, and the expected result (F-15)."""

    model_config = ConfigDict(frozen=True)

    what: str = Field(min_length=1, description="What is verified")
    how: str = Field(min_length=1, description="How the check is performed")
    expected: str = Field(min_length=1, description="The expected outcome")


class RollbackStep(BaseModel):
    """How to undo a task: trigger condition, action, and safety guard.

    Starting in v0.2.2, high-blast-radius tasks should also declare what
    state they restore and how that restoration is verified. These fields
    are optional — when absent, the ``rollback_credible`` gate derives
    credibility from surrounding structure as before (backward-compatible).
    """

    model_config = ConfigDict(frozen=True)

    trigger: str = Field(min_length=1, description="What condition triggers the rollback")
    action: str = Field(min_length=1, description="What the rollback does")
    safety_guard: str = Field(default="", description="Guard that must hold before rolling back")
    restores_state: list[str] | None = Field(
        default=None,
        description="Facts the undo re-establishes (referencing precondition ledger fact keys). "
        "``None`` means legacy prose-only rollback (backward-compatible). "
        "An empty list ``[]`` means the author explicitly declined to declare state. "
        "When present, the rollback_credible gate validates these against the plan graph. "
        "When absent (None), credibility is derived from surrounding structure.",
    )
    restoration_evidence: str | None = Field(
        default=None,
        description="How to verify the state was restored after rollback. "
        "When present, rollback_credible treats it as satisfying the "
        "re-establishment exemption for inconsistent-state / post-consumed patterns.",
    )

    @field_validator("restores_state", mode="before")
    @classmethod
    def _coerce_restores_state(cls, value: object) -> object:
        """Leniently accept LLM-produced values for ``restores_state``.

        LLM planners do not read the schema and frequently emit a bare string
        (e.g. ``"restore components to pre-fix state"``) where a ``list[str]``
        is expected, which strict Pydantic validation rejects — failing the
        plan after the retry budget (see v0.2.2 field-test learning L-1).
        Coerce ``str`` → ``[str]`` so LLM-produced plans validate while the
        typed ``list[str] | None`` contract is preserved for code-produced plans.
        """
        if isinstance(value, str):
            return [value]
        return value


class EnvProbe(BaseModel):
    """Optional probe grounding a precondition in live state (F-19).

    Read-only by contract — a probe observes, never mutates. Deterministic
    gates never depend on a probe; probes enrich execution-time re-gating.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(description="e.g. env_var | db_query | http_check | deploy_status")
    query: str = Field(min_length=1)
    expected: str = Field(min_length=1)


class Precondition(BaseModel):
    """A condition that must hold before a task may start.

    ``fact`` names what must be established; ``established_by`` references
    the earlier task or env fact that establishes it (checked by the
    ``preconditions_referenced`` gate). An optional :class:`EnvProbe`
    lets the re-gate re-verify the fact at execution time.
    """

    model_config = ConfigDict(frozen=True)

    description: str = Field(min_length=1)
    fact: str = Field(min_length=1, description="The fact that must be established")
    established_by: str | None = Field(
        default=None, description="Earlier task id or env-fact name that establishes the fact"
    )
    probe: EnvProbe | None = None


class Task(BaseModel):
    """A single step in the plan (F-02)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    description: str = Field(min_length=1, description="What this step accomplishes")
    action: str = Field(default="", description="Verb: the operation performed")
    target: str = Field(default="", description="The object the action operates on")
    parallel_group: str | None = Field(
        default=None, description="Tasks with the same group id run concurrently"
    )
    preconditions: list[Precondition] = Field(default_factory=list)
    verification: VerificationStep | None = None
    rollback: RollbackStep | None = None
    risk_class: RiskClass = RiskClass.MEDIUM
    blast_radius: str = Field(default="medium", description="low|medium|high|critical")
    satisfies: str | None = Field(
        default=None,
        description="Reference to the acceptance criterion this step satisfies. "
        "When set, the requirement-traceability gate verifies this criterion "
        "is bound to the goal. Optional for backward compatibility.",
    )


class Dependency(BaseModel):
    """An ordering edge between two tasks."""

    model_config = ConfigDict(frozen=True)

    from_task: str = Field(min_length=1)
    to_task: str = Field(min_length=1)
    kind: DependencyKind = DependencyKind.HARD
    reason: str = Field(default="", description="Why this dependency exists")


class Branch(BaseModel):
    """Fan-out / fan-in shape over a set of tasks (F-15)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    kind: BranchKind
    tasks: list[str] = Field(min_length=1)
    join: JoinMode = JoinMode.ALL


class PlanVersion(BaseModel):
    """One immutable revision of a plan.

    Once constructed, a ``PlanVersion`` never mutates. New revisions are new
    instances with ``version`` incremented and ``parent_version`` pointing at
    the id of the previous revision.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    plan_schema_version: str = PLAN_SCHEMA_VERSION
    version: int = Field(ge=1)

    #: Id of the parent plan version this revision revises; None for root plans.
    parent_version: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tasks: list[Task]
    dependencies: list[Dependency] = Field(default_factory=list)
    branches: list[Branch] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_graph(self) -> PlanVersion:
        """Enforce structural invariants: unique ids, resolvable references."""
        task_ids = {task.id for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise ValueError(f"duplicate task id in plan {self.id}")

        for dep in self.dependencies:
            if dep.from_task not in task_ids or dep.to_task not in task_ids:
                raise ValueError(
                    f"dependency {dep.from_task}->{dep.to_task} references an unknown task"
                )
            if dep.from_task == dep.to_task:
                raise ValueError(f"self-referential dependency on {dep.from_task}")

        for branch in self.branches:
            unknown = [tid for tid in branch.tasks if tid not in task_ids]
            if unknown:
                raise ValueError(f"branch {branch.id} references unknown tasks: {unknown}")

        by_group: dict[str, list[str]] = {}
        for task in self.tasks:
            if task.parallel_group is not None:
                by_group.setdefault(task.parallel_group, []).append(task.id)

        for group, members in by_group.items():
            if len(set(members)) != len(members):
                raise ValueError(f"parallel_group {group} lists a task more than once")
            hard_edges = {
                (dep.from_task, dep.to_task)
                for dep in self.dependencies
                if dep.kind is DependencyKind.HARD
            }
            for left in members:
                for right in members:
                    if left != right and (left, right) in hard_edges:
                        raise ValueError(
                            f"tasks {left}->{right} are both in parallel_group {group} "
                            "but have a hard dependency between them"
                        )
        return self

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly serialization (lossless via the typed schema)."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PlanVersion:
        """Reconstruct a plan from :meth:`to_dict` output."""
        return cls.model_validate(data)
