"""The pluggable plan store (F-09, PRD §2.1): versioned plan persistence.

The store is a **side channel**: planning proceeds in memory if the store is
down (with a warning), and is persisted when healthy (§7.2). The protocol is
kept framework-agnostic — :class:`PlanStore` speaks typed Pydantic models, and
a concrete implementation (in-memory for tests, SQLite for production) merely
has to round-trip them losslessly.

Every plan version, its critique findings, escalations, and execution traces
are first-class, so the full draft→critique→revise→approve→execute history is
diff-able and replay-able (§2.1 items 5 and 7).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..schema.plan import Dependency, PlanVersion
from ..schema.acceptance import AcceptanceContract
from ..types import Escalation, ExecutionTrace, Finding
from .replan_trace import ReplanLink

logger = logging.getLogger(__name__)


class PlanDiff(BaseModel):
    """Structural difference between two revisions of the same plan (F-09).

    Produced by :meth:`PlanStore.diff`. ``changed_task_ids`` lists tasks that
    exist in both revisions but whose serialized body differs; dependencies and
    branches are compared by identity so additions/removals are explicit.
    """

    model_config = ConfigDict(frozen=True)

    plan_id: str
    from_version: int
    to_version: int
    added_task_ids: list[str] = Field(default_factory=list)
    removed_task_ids: list[str] = Field(default_factory=list)
    changed_task_ids: list[str] = Field(default_factory=list)
    added_dependencies: list[Dependency] = Field(default_factory=list)
    removed_dependencies: list[Dependency] = Field(default_factory=list)
    added_branches: list[str] = Field(default_factory=list)
    removed_branches: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when both revisions are structurally identical."""
        return not (
            self.added_task_ids
            or self.removed_task_ids
            or self.changed_task_ids
            or self.added_dependencies
            or self.removed_dependencies
            or self.added_branches
            or self.removed_branches
        )


class PlanStore(ABC):
    """Versioned plan store protocol (§2.1).

    Implementations must be **side-channel safe**: a store that is down must
    raise :class:`StoreUnavailable` so the caller can warn and continue in
    memory — it must never silently corrupt or drop data.
    """

    @abstractmethod
    def put_plan_version(self, plan: PlanVersion) -> None:
        """Persist a plan revision (immutable once written).

        Args:
            plan: The revision to store.
        """

    @abstractmethod
    def put_findings(self, plan_id: str, version: int, findings: list[Finding]) -> None:
        """Persist the critique findings produced against one revision.

        Args:
            plan_id: Plan whose revision the findings target.
            version: Revision number the findings were produced against.
            findings: The findings to store.
        """

    @abstractmethod
    def get_plan(self, plan_id: str, version: int | None = None) -> PlanVersion | None:
        """Fetch a plan revision.

        Args:
            plan_id: Plan to fetch.
            version: Revision number; None means the latest.

        Returns:
            The requested revision, or None if it does not exist.
        """

    @abstractmethod
    def get_findings(self, plan_id: str, version: int) -> list[Finding]:
        """Fetch the critique findings produced against one revision.

        Args:
            plan_id: Plan whose revision the findings target.
            version: Revision number.

        Returns:
            The findings stored for that revision (empty if none).
        """

    @abstractmethod
    def list_plans(self, goal_id: str | None = None) -> list[PlanVersion]:
        """List stored plan revisions, newest first.

        Args:
            goal_id: Restrict to plans serving this goal.

        Returns:
            All revisions (optionally for one goal), newest first.
        """

    @abstractmethod
    def diff(self, plan_id: str, version_a: int, version_b: int) -> PlanDiff | None:
        """Diff two revisions of one plan.

        Args:
            plan_id: Plan to diff.
            version_a: Older revision number.
            version_b: Newer revision number.

        Returns:
            The structural difference, or None if either revision is unknown.
        """

    @abstractmethod
    def put_escalation(self, escalation: Escalation) -> None:
        """Persist an escalation record.

        Args:
            escalation: The escalation to store.
        """

    @abstractmethod
    def get_escalation(self, plan_id: str) -> Escalation | None:
        """Fetch the escalation for a plan, if any.

        Args:
            plan_id: Plan whose escalation to fetch.

        Returns:
            The escalation, or None if the plan was never escalated.
        """

    @abstractmethod
    def put_execution_trace(self, trace: ExecutionTrace) -> None:
        """Persist one step of an execution trace.

        Args:
            trace: The execution step to store.
        """

    @abstractmethod
    def get_execution_traces(self, plan_id: str) -> list[ExecutionTrace]:
        """Fetch all recorded execution steps for a plan.

        Args:
            plan_id: Plan whose execution trace to fetch.

        Returns:
            The recorded execution steps, in insertion order.
        """

    @abstractmethod
    def link(self, plan_id: str, version: int, trace_id: str) -> None:
        """Link an approved plan revision to an execution trace (F-51).

        Args:
            plan_id: The approved plan.
            version: The approved revision number.
            trace_id: The execution trace the plan was run against.
        """

    @abstractmethod
    def put_replan_link(self, link: ReplanLink) -> None:
        """Record a replan linkage (F-53).

        Args:
            link: The replan link to persist.
        """

    @abstractmethod
    def get_replan_link(self, plan_id: str, version: int) -> ReplanLink | None:
        """Fetch the replan link for a revision, if any.

        Args:
            plan_id: The replanned plan.
            version: The replanned revision number.

        Returns:
            The replan link, or None if the revision was not a replan.
        """

    @abstractmethod
    def get_child_replan_links(self, parent_plan_id: str, parent_version: int) -> list[ReplanLink]:
        """Fetch all replan links that point at a given parent revision.

        Args:
            parent_plan_id: The parent plan id.
            parent_version: The parent revision number.

        Returns:
            All replan links whose parent is the given revision.
        """

    @abstractmethod
    def put_missed_critique(self, plan_id: str, body: str) -> None:
        """Persist a missed-critique record (F-51).

        Args:
            plan_id: The plan the missed critique belongs to.
            body: JSON-serialized missed-critique record.
        """

    @abstractmethod
    def get_missed_critique(self, plan_id: str) -> str | None:
        """Fetch the missed-critique record for a plan, if any.

        Args:
            plan_id: The plan to look up.

        Returns:
            The JSON body, or None if no missed critique was recorded.
        """

    @abstractmethod
    def put_plan_signature(self, plan_id: str, version: int, signature: str) -> None:
        """Persist a plan's structural signature for oscillation analysis.

        Args:
            plan_id: The plan id.
            version: The revision number.
            signature: The structural signature string.
        """

    @abstractmethod
    def get_plan_signatures(self, plan_id: str) -> list[tuple[int, str]]:
        """Fetch all stored signatures for a plan, newest first.

        Args:
            plan_id: The plan to look up.

        Returns:
            List of ``(version, signature)`` tuples, newest first.
        """

    @abstractmethod
    def close(self) -> None:
        """Close the store and release resources."""

    @abstractmethod
    def put_acceptance_contract(self, contract: AcceptanceContract) -> None:
        """Persist an acceptance contract for a goal.

        Args:
            contract: The contract to store, keyed by goal_id.
        """

    @abstractmethod
    def get_acceptance_contract(self, goal_id: str) -> AcceptanceContract | None:
        """Fetch the acceptance contract for a goal, if any.

        Args:
            goal_id: The goal whose contract to fetch.

        Returns:
            The bound contract, or None if no contract exists.
        """

    def warn_and_continue(self, err: Exception) -> None:
        """Side-channel contract (§7.2): warn, then let the caller continue.

        Args:
            err: The failure that made the store unavailable.
        """
        logger.warning(
            "Plan store unavailable (%s: %s); continuing in memory — data will "
            "not be persisted until the store recovers",
            type(err).__name__,
            err,
        )


class StoreUnavailable(Exception):
    """Raised when a store cannot serve a request (down, locked, corrupt).

    Callers catch this to trigger the side-channel warn-and-continue path
    instead of failing the planning run.
    """

    kind: Literal["unavailable"] = "unavailable"


class InMemoryStore(PlanStore):
    """Test-focused in-memory store; never fails (always the fallback).

    Keeps the same keying scheme as SQLite so a run can be moved between the
    two without changing the protocol: plans and findings are keyed by
    ``(plan_id, version)``, escalations and traces by ``plan_id``.
    """

    def __init__(self) -> None:
        """Initialize empty indexes."""
        self._plans: dict[tuple[str, int], PlanVersion] = {}
        self._findings: dict[tuple[str, int], list[Finding]] = {}
        self._escalations: dict[str, Escalation] = {}
        self._traces: dict[str, list[ExecutionTrace]] = {}
        self._links: set[tuple[str, int, str]] = set()
        self._replan_links: dict[tuple[str, int], ReplanLink] = {}
        self._missed_critiques: dict[str, str] = {}
        self._signatures: dict[tuple[str, int], str] = {}
        self._contracts: dict[str, AcceptanceContract] = {}

    def close(self) -> None:
        """No-op for in-memory store."""

    def put_plan_version(self, plan: PlanVersion) -> None:
        """Persist a plan revision in the in-memory index."""
        self._plans[(plan.id, plan.version)] = plan

    def put_findings(self, plan_id: str, version: int, findings: list[Finding]) -> None:
        """Persist findings under the ``(plan_id, version)`` key."""
        self._findings[(plan_id, version)] = list(findings)

    def get_findings(self, plan_id: str, version: int) -> list[Finding]:
        """Return the findings stored for one revision (empty if none)."""
        return list(self._findings.get((plan_id, version), []))

    def get_plan(self, plan_id: str, version: int | None = None) -> PlanVersion | None:
        """Return the requested revision, or the latest when version is None."""
        if version is not None:
            return self._plans.get((plan_id, version))
        versions = [v for (pid, v) in self._plans if pid == plan_id]
        if not versions:
            return None
        return self._plans[(plan_id, max(versions))]

    def list_plans(self, goal_id: str | None = None) -> list[PlanVersion]:
        """Return stored revisions (optionally per goal), newest first."""
        plans = [p for p in self._plans.values() if goal_id is None or p.goal_id == goal_id]
        return sorted(plans, key=lambda p: (p.id, -p.version))

    def diff(self, plan_id: str, version_a: int, version_b: int) -> PlanDiff | None:
        """Diff two revisions structurally; None when either is unknown."""
        a = self._plans.get((plan_id, version_a))
        b = self._plans.get((plan_id, version_b))
        if a is None or b is None:
            return None
        return _compute_diff(a, b)

    def put_escalation(self, escalation: Escalation) -> None:
        """Persist an escalation by plan id."""
        self._escalations[escalation.plan_id] = escalation

    def get_escalation(self, plan_id: str) -> Escalation | None:
        """Return the escalation for a plan, if any."""
        return self._escalations.get(plan_id)

    def put_execution_trace(self, trace: ExecutionTrace) -> None:
        """Append an execution step to the plan's trace."""
        self._traces.setdefault(trace.plan_id, []).append(trace)

    def get_execution_traces(self, plan_id: str) -> list[ExecutionTrace]:
        """Return the recorded execution steps for a plan, in order."""
        return list(self._traces.get(plan_id, []))

    def link(self, plan_id: str, version: int, trace_id: str) -> None:
        """Record the approved-revision ↔ execution-trace link."""
        self._links.add((plan_id, version, trace_id))

    def put_replan_link(self, link: ReplanLink) -> None:
        """Record a replan linkage by (plan_id, version)."""
        self._replan_links[(link.plan_id, link.version)] = link

    def get_replan_link(self, plan_id: str, version: int) -> ReplanLink | None:
        """Return the replan link for a revision, or None."""
        return self._replan_links.get((plan_id, version))

    def get_child_replan_links(self, parent_plan_id: str, parent_version: int) -> list[ReplanLink]:
        """Return all replan links whose parent is the given revision."""
        return [
            link
            for link in self._replan_links.values()
            if link.parent_plan_id == parent_plan_id and link.parent_version == parent_version
        ]

    def put_missed_critique(self, plan_id: str, body: str) -> None:
        """Persist a missed-critique record by plan id."""
        self._missed_critiques[plan_id] = body

    def get_missed_critique(self, plan_id: str) -> str | None:
        """Return the missed-critique JSON for a plan, or None."""
        return self._missed_critiques.get(plan_id)

    def put_plan_signature(self, plan_id: str, version: int, signature: str) -> None:
        """Persist a plan signature keyed by (plan_id, version)."""
        self._signatures[(plan_id, version)] = signature

    def get_plan_signatures(self, plan_id: str) -> list[tuple[int, str]]:
        """Return stored signatures for a plan, newest first."""
        items = [(v, s) for (pid, v), s in self._signatures.items() if pid == plan_id]
        return sorted(items, key=lambda x: -x[0])

    def put_acceptance_contract(self, contract: AcceptanceContract) -> None:
        """Persist an acceptance contract by goal_id."""
        self._contracts[contract.goal_id] = contract

    def get_acceptance_contract(self, goal_id: str) -> AcceptanceContract | None:
        """Return the acceptance contract for a goal, or None."""
        return self._contracts.get(goal_id)


def _compute_diff(a: PlanVersion, b: PlanVersion) -> PlanDiff:
    """Compute the structural difference between two revisions.

    Args:
        a: The older revision.
        b: The newer revision.

    Returns:
        A :class:`PlanDiff` describing what changed between them.
    """
    a_tasks = {t.id: t for t in a.tasks}
    b_tasks = {t.id: t for t in b.tasks}

    added = [tid for tid in b_tasks if tid not in a_tasks]
    removed = [tid for tid in a_tasks if tid not in b_tasks]
    changed = [
        tid
        for tid in a_tasks.keys() & b_tasks.keys()
        if a_tasks[tid].model_dump(mode="json") != b_tasks[tid].model_dump(mode="json")
    ]

    a_deps = {(d.from_task, d.to_task): d for d in a.dependencies}
    b_deps = {(d.from_task, d.to_task): d for d in b.dependencies}
    added_deps = [b_deps[k] for k in b_deps if k not in a_deps]
    removed_deps = [a_deps[k] for k in a_deps if k not in b_deps]

    a_branches = {br.id for br in a.branches}
    b_branches = {br.id for br in b.branches}

    return PlanDiff(
        plan_id=a.id,
        from_version=a.version,
        to_version=b.version,
        added_task_ids=added,
        removed_task_ids=removed,
        changed_task_ids=changed,
        added_dependencies=added_deps,
        removed_dependencies=removed_deps,
        added_branches=[bid for bid in b_branches - a_branches],
        removed_branches=[bid for bid in a_branches - b_branches],
    )
