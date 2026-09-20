"""Core data types for the PlannerCritic engine.

These are strict Pydantic v2 models with ``frozen=True``: a validated value
cannot be mutated after construction, so a finding, verdict, or approved plan
that flows into the store or out to an executor is stable and auditable. The
shape of every model mirrors the PRD typed sketch (§2.8) so serialization
round-trips are lossless.

The one security-critical type is :class:`ApprovedPlan`: it is the *only*
type that may be handed to an executor. See :mod:`planner_critic.approval`
for the fail-closed construction rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .reason_codes import ReasonCode
from .schema.goal import RiskTolerance
from .schema.plan import PlanVersion

FINDING_SCHEMA_VERSION = "0.2.0"


class Severity(StrEnum):
    """Graded severity of a critique finding."""

    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"

    @property
    def is_blocker(self) -> bool:
        """True when this severity can never co-exist with approval."""
        return self is Severity.BLOCKER


class HeuristicFamily(StrEnum):
    """The six critique heuristic families that produce a finding."""

    FEASIBILITY = "feasibility"
    RISK = "risk"
    MISSING_STEPS = "missing_steps"
    UNSAFE_SEQUENCING = "unsafe_sequencing"
    UNVERIFIED_DEPENDENCIES = "unverified_dependencies"
    WEAK_ROLLBACK = "weak_rollback"

    def __str__(self) -> str:
        """Human-friendly family label for logs and messages."""
        return self.value


class Finding(BaseModel):
    """A single critique result against one task (or the whole plan).

    ``task_id`` is nullable because some deterministic gates (e.g. a
    dependency cycle) flag the plan as a whole rather than a single task.

    ``raw_severity`` is the severity the LLM critic originally assigned;
    ``normalized_severity`` is what the code enforced after the frozenset
    guardrail. ``drift_delta`` = normalized - raw (negative = downgrade,
    zero = no drift). Both fields default to ``severity`` for backward
    compatibility with legacy findings.

    ``edge_id`` captures the producer→consumer pair for ordering-family
    defects, so the planner revises against a concrete missing edge rather
    than a prose message. ``observed_state`` records what the gate actually
    observed. ``evidence_refs`` points to the structural facts that fired.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Stable unique id for the finding")
    task_id: str | None = Field(
        default=None, description="Target task id, or None for plan-level findings"
    )
    version: int = Field(description="Plan version the finding was produced against")
    heuristic_family: HeuristicFamily | None = Field(
        default=None,
        description="Which heuristic family produced this finding (None for structural gates)",
    )
    severity: Severity
    reason_code: ReasonCode = Field(description="Stable machine-readable reason code")
    message: str = Field(description="Human-readable description of the problem")
    suggested_fix: str | None = Field(default=None, description="Optional suggested remediation")
    raw_severity: Severity | None = Field(
        default=None,
        description="Severity assigned by the LLM critic (None for legacy/deterministic)",
    )
    normalized_severity: Severity | None = Field(
        default=None, description="Severity after frozenset guardrail enforcement (None for legacy)"
    )
    drift_delta: int = Field(
        default=0,
        description="normalized_severity - raw_severity (negative = downgrade, zero = no drift)",
    )
    edge_id: str | None = Field(
        default=None,
        description="Canonical producer→consumer edge for ordering defects, "
        "e.g. 'pred->succ'. None for task-level findings.",
    )
    observed_state: str | None = Field(
        default=None,
        description="One-line factual observation the defect rests on, "
        "e.g. 'verification after consumer ran'.",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Stable pointers to the structural facts that fired: "
        "precondition fact keys, dependency-edge ids, action-registry entries.",
    )
    finding_schema_version: str = Field(
        default=FINDING_SCHEMA_VERSION,
        description="Schema version of this finding. Bumped when finding semantics change.",
    )

    def __str__(self) -> str:
        """Log-friendly one-line rendering."""
        return f"[{self.severity.value}] {self.reason_code}: {self.message}"

    @property
    def is_llm_finding(self) -> bool:
        """True when this finding came from the LLM critic (not a gate).

        LLM findings carry a :class:`HeuristicFamily`; deterministic gate
        findings do not. This discriminator lets the loop apply different
        termination rules: gate blockers are hard (deterministic,
        reproducible); LLM blockers are advisory (probabilistic, may vary
        across audits of the same plan).
        """
        return self.heuristic_family is not None


class Escalation(BaseModel):
    """A minimal, precise question to a human when the loop cannot converge."""

    model_config = ConfigDict(frozen=True)

    id: str
    plan_id: str
    version: int
    blocker_finding_id: str | None = None
    question: str = Field(description="The precise question blocking approval")
    status: Literal["open", "approved", "denied"] = "open"
    resolution: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = Field(
        default=None, description="Who resolved this escalation (principal name)"
    )


class ExecutionTrace(BaseModel):
    """Links an approved plan to a later run; classifies a failure."""

    model_config = ConfigDict(frozen=True)

    id: str
    plan_id: str
    task_id: str
    outcome: str
    failure_class: Literal["planning", "execution"] | None = None
    linked_finding_id: str | None = Field(
        default=None, description="Missed-critique link (F-51/F-52)"
    )


class PlanComplexity(BaseModel):
    """Deterministic, derived, pre-approval summary of a plan's cost/risk."""

    model_config = ConfigDict(frozen=True)

    step_count: int
    parallel_branch_count: int
    irreversible_op_count: int
    est_llm_calls: int
    est_token_cost: float


class ApprovedPlan(BaseModel):
    """The only executable artifact — fail-closed construction is mandatory.

    Instantiate via :func:`planner_critic.approval.approve`, never directly:
    an `ApprovedPlan` can only exist when the approval threshold for the
    goal's ``risk_tolerance`` is met. See F-08 / F-73.
    """

    model_config = ConfigDict(frozen=True)

    plan: PlanVersion
    findings: list[Finding] = Field(
        default_factory=list, description="Warnings acknowledged at approval time"
    )
    risk_tolerance: RiskTolerance
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def goal_id(self) -> str:
        """Goal this approved plan serves."""
        return self.plan.goal_id


class PlanningError(Exception):
    """A planner/critic role could not produce a valid result.

    Raised when a provider fails (timeout, malformed JSON, schema mismatch)
    in a way that must fail closed rather than hand garbage upstream. Carries
    a stable :class:`ReasonCode` so the loop surfaces ``planning_unavailable``
    per role instead of an opaque exception.
    """

    def __init__(self, message: str, reason_code: ReasonCode = "planning_unavailable") -> None:
        """Initialize with a message and the fail-closed reason code."""
        super().__init__(message)
        self.reason_code = reason_code
