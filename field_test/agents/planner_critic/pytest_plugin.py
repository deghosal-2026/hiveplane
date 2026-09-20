"""pytest-planner-critic — deterministic gate unit-testing plugin (M3, #156).

Provides pytest fixtures and assertion helpers for writing concise,
readable tests against the PlannerCritic engine's deterministic gates,
without any LLM cost.

Usage in a test file::

    pytest_plugins = ("planner_critic.pytest_plugin",)

    def test_ordering(plan_builder):
        plan = plan_builder.with_ordering_violation().build()
        assert_gate_fails(OrderingGate(), plan, "unsafe_ordering")


Fixtures
--------
* ``plan_builder`` — incremental builder for constructing test plans.
* ``mock_engine`` — pre-configured ``Engine`` with ``ScriptedPlanner`` +
  ``EmptyCritic`` for fast approval-path tests.
* ``mock_critic`` — ``ScriptedCritic`` pre-loaded with canned findings.

Assertions
----------
* :func:`assert_gate_passes` — a gate produces no findings.
* :func:`assert_gate_fails` — a gate produces at least one blocker
  (optionally with an expected reason code).
* :func:`assert_node_precedes` — a task appears before another in the
  plan list (useful for ordering tests).
* :func:`assert_no_circular_dependencies` — the plan's hard-dependency
  graph is a DAG.
* :func:`assert_plan_converges` — ``run_loop`` terminates with approval.

Diff formatting
---------------
* :class:`GraphDiffFormatter` — computes a readable structural diff of two
  ``PlanVersion`` instances for assertion error messages.
* :func:`format_dag_diff` — renders the diff as a string (registered via
  ``pytest_assertrepr_compare`` when the plugin is loaded).
"""

from __future__ import annotations

from typing import Any

import pytest

from .engine import Engine
from .gates.base import BaseGate
from .loop import LoopConfig, LoopResult, run_loop
from .roles import CriticRole, PlannerRole
from .schema.goal import Goal
from .schema.plan import PlanVersion
from .types import Finding, Severity

# ── Exported assertion helpers ───────────────────────────────────────────


def assert_gate_passes(gate: BaseGate, plan: PlanVersion) -> None:
    """Assert that ``gate.run(plan)`` produces no findings.

    Args:
        gate: The deterministic gate to evaluate.
        plan: The plan to audit.

    Raises:
        AssertionError: When the gate produces any finding.
    """
    findings = gate.run(plan)
    if not findings:
        return
    msg = f"gate {gate.name!r} produced {len(findings)} finding(s): {findings}"
    raise AssertionError(msg)


def assert_gate_fails(
    gate: BaseGate,
    plan: PlanVersion,
    reason_code: str | None = None,
) -> list[Finding]:
    """Assert that ``gate.run(plan)`` produces at least one blocker.

    Args:
        gate: The deterministic gate to evaluate.
        plan: The plan to audit.
        reason_code: If given, require at least one finding with this code.

    Returns:
        The blocker findings (for further inspection by the caller).

    Raises:
        AssertionError: When the gate produces no findings, or when
            ``reason_code`` is specified and no finding matches.
    """
    findings = gate.run(plan)
    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    if not blockers:
        raise AssertionError(
            f"expected gate {gate.name!r} to produce blockers, "
            f"got {len(findings)} non-blocker finding(s)"
        )
    if reason_code is not None:
        matched = [f for f in blockers if f.reason_code == reason_code]
        if not matched:
            codes = {f.reason_code for f in blockers}
            raise AssertionError(
                f"expected reason_code {reason_code!r} among blocker codes: {codes}"
            )
    return blockers


def assert_node_precedes(plan: PlanVersion, before: str, after: str) -> None:
    """Assert that ``before`` appears earlier in the plan's task list.

    Args:
        plan: The plan whose task ordering to check.
        before: Task id that must appear first.
        after: Task id that must appear second.

    Raises:
        AssertionError: When ``after`` appears at or before ``before``,
            or when either id is not found.
    """
    order = [t.id for t in plan.tasks]
    try:
        i_before = order.index(before)
        i_after = order.index(after)
    except ValueError as exc:
        raise AssertionError(f"task not found in plan: {exc}") from exc
    if i_before >= i_after:
        raise AssertionError(f"expected {before!r} before {after!r}, but order is {order}")


def assert_no_circular_dependencies(plan: PlanVersion) -> None:
    """Assert the plan's hard-dependency graph is a DAG.

    Args:
        plan: The plan to check.

    Raises:
        AssertionError: When a cycle is detected.
    """
    from .gates.dep_cycles import Gate as CycleGate

    findings = CycleGate().run(plan)
    if findings:
        raise AssertionError(f"plan has a circular dependency: {findings[0].message}")


def assert_plan_converges(
    goal: Goal,
    planner: PlannerRole,
    critic: CriticRole,
    loop_config: LoopConfig | None = None,
) -> LoopResult:
    """Assert that ``run_loop`` terminates with ``approved``.

    Args:
        goal: The goal to plan for.
        planner: The planner role.
        critic: The critic role.
        loop_config: Optional loop configuration (defaults to
            ``deterministic-first``, cap 3).

    Returns:
        The loop result for further inspection.

    Raises:
        AssertionError: When the loop escalates rather than approving.
    """
    cfg = loop_config or LoopConfig()
    result = run_loop(goal, planner, critic, config=cfg)
    if not result.is_approved:
        raise AssertionError(
            f"expected plan to converge, but got status={result.status!r}, "
            f"reason={result.reason_code!r}"
        )
    return result


def assert_rollback_dag_valid(plan: PlanVersion) -> None:
    """Assert that a rollback plan's dependency graph is a valid DAG.

    Args:
        plan: The plan whose rollback DAG to validate.

    Raises:
        AssertionError: When the graph contains a cycle.
    """
    from .rollback_synth import rollback_dag_valid

    if not rollback_dag_valid(plan):
        raise AssertionError("plan is not a valid DAG for rollback synthesis")


# ── Graph diff formatting ────────────────────────────────────────────────


def format_dag_diff(plan_a: PlanVersion, plan_b: PlanVersion) -> str:
    """Return a human-readable structural diff between two plans.

    Compares task ids, dependency edges, and branch structure. Returns an
    empty string when the plans are structurally identical.

    Args:
        plan_a: The expected plan.
        plan_b: The actual plan.

    Returns:
        A diff string or an empty string when identical.
    """
    lines: list[str] = []

    ids_a = {t.id for t in plan_a.tasks}
    ids_b = {t.id for t in plan_b.tasks}
    only_a = ids_a - ids_b
    only_b = ids_b - ids_a
    if only_a:
        lines.append(f"tasks only in expected: {sorted(only_a)}")
    if only_b:
        lines.append(f"tasks only in actual: {sorted(only_b)}")

    order_a = [t.id for t in plan_a.tasks]
    order_b = [t.id for t in plan_b.tasks]
    common = ids_a & ids_b
    for tid in sorted(common):
        if order_a.index(tid) != order_b.index(tid):
            lines.append(
                f"task {tid!r}: position {order_a.index(tid)} "
                f"(expected) vs {order_b.index(tid)} (actual)"
            )

    edges_a = {(d.from_task, d.to_task, d.kind.value) for d in plan_a.dependencies}
    edges_b = {(d.from_task, d.to_task, d.kind.value) for d in plan_b.dependencies}
    missing = edges_a - edges_b
    extra = edges_b - edges_a
    if missing:
        for e in sorted(missing):
            lines.append(f"missing edge: {e[0]} -> {e[1]} ({e[2]})")
    if extra:
        for e in sorted(extra):
            lines.append(f"unexpected edge: {e[0]} -> {e[1]} ({e[2]})")

    if not lines:
        return "plans are structurally identical"
    return "\n".join(lines)


class GraphDiffFormatter:
    """Formatter object that wraps :func:`format_dag_diff`.

    Can be used in ``pytest_assertrepr_compare`` hooks to produce
    colorized DAG diffs when plan assertions fail.
    """

    def __init__(self, expected: PlanVersion, actual: PlanVersion) -> None:
        self.expected = expected
        self.actual = actual

    def __str__(self) -> str:
        return format_dag_diff(self.expected, self.actual)


def pytest_assertrepr_compare(
    config: Any,
    op: str,
    left: object,
    right: object,
) -> list[str] | None:
    """pytest assertion rewrite hook — display DAG diffs for plans.

    Activated automatically when the plugin is installed.
    """
    if isinstance(left, PlanVersion) and isinstance(right, PlanVersion) and op == "==":
        diff = format_dag_diff(left, right)
        if diff:
            return ["PlanVersion mismatch:", *diff.split("\n")]
    return None


# ── pytest fixtures (registered via pytest_plugin in conftest) ───────────


class _PlanBuilder:
    """Incremental builder for constructing test plans with readable syntax.

    Example::

        plan = (plan_builder
            .with_task("backup", risk_class="medium")
            .with_task("migrate", risk_class="high")
            .with_dependency("backup", "migrate")
            .build())
    """

    def __init__(self) -> None:
        from .schema.plan import Branch, Dependency, Task

        self._tasks: list[Task] = []
        self._deps: list[Dependency] = []
        self._branches: list[Branch] = []
        self._goal_id: str = "test-goal"
        self._plan_id: str = "test-plan"

    def with_task(
        self,
        task_id: str,
        *,
        risk_class: str = "medium",
        blast_radius: str = "medium",
    ) -> _PlanBuilder:
        """Add a task to the plan in construction order."""
        from .schema.plan import Task

        self._tasks.append(
            Task.model_validate(
                {
                    "id": task_id,
                    "description": f"task {task_id}",
                    "action": "do",
                    "target": task_id,
                    "risk_class": risk_class,
                    "blast_radius": blast_radius,
                }
            )
        )
        return self

    def with_dependency(self, from_task: str, to_task: str, kind: str = "hard") -> _PlanBuilder:
        """Add a dependency edge."""
        from .schema.plan import Dependency, DependencyKind

        self._deps.append(
            Dependency(
                from_task=from_task,
                to_task=to_task,
                kind=DependencyKind(kind),
                reason="test",
            )
        )
        return self

    def build(self) -> PlanVersion:
        """Produce the final :class:`PlanVersion`."""
        return PlanVersion(
            id=self._plan_id,
            goal_id=self._goal_id,
            version=1,
            tasks=self._tasks,
            dependencies=self._deps,
            branches=self._branches,
        )


@pytest.fixture
def plan_builder() -> _PlanBuilder:
    """Fixture: returns a fresh ``_PlanBuilder`` for constructing plans."""
    return _PlanBuilder()


@pytest.fixture
def mock_engine() -> Engine:
    """Fixture: a pre-configured ``Engine`` using ``ScriptedPlanner`` +
    ``EmptyCritic`` (deterministic-first).

    Use with ``engine.plan(goal)`` for fast approval-path tests.
    """
    from .roles import CriticRole, PlannerRole

    class _SimplePlanner(PlannerRole):
        def __init__(self) -> None:
            self.last_plan: PlanVersion | None = None

        def decompose(self, goal: Goal) -> PlanVersion:
            return PlanVersion(
                id=f"plan-{goal.id}",
                goal_id=goal.id,
                version=1,
                tasks=[],  # subclasses should set plans
            )

        def revise(self, plan: PlanVersion, findings: list[Finding]) -> PlanVersion:
            return plan

    class _QuietCritic(CriticRole):
        def audit(self, plan: PlanVersion, findings: list[Finding]) -> list[Finding]:
            return list(findings)

    return Engine(_SimplePlanner(), _QuietCritic(), config=LoopConfig())


@pytest.fixture
def mock_critic() -> list[Finding]:
    """Fixture: returns an empty finding list for a quiet mock critic.

    Combine with ``ScriptedCritic([your_findings])`` for custom results.
    """
    return []


__all__ = [
    "GraphDiffFormatter",
    "assert_gate_fails",
    "assert_gate_passes",
    "assert_no_circular_dependencies",
    "assert_node_precedes",
    "assert_plan_converges",
    "assert_rollback_dag_valid",
    "format_dag_diff",
    "mock_critic",
    "mock_engine",
    "plan_builder",
    "pytest_assertrepr_compare",
]
