from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, TypeVar, cast

from .engine import Engine
from .loop import LoopConfig
from .roles import CriticRole, PlannerRole
from .schema.goal import Goal, RiskTolerance
from .types import Finding

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class EscalationRequired(Exception):
    """Raised when a @guardrail-decorated function's plan is escalated.

    Carries the escalation reason code and any findings so the caller can
    inspect why the gate blocked execution.
    """

    def __init__(
        self, message: str, reason_code: str = "", findings: list[Finding] | None = None
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.findings = findings or []


class PreconditionDrift(Exception):
    """Raised by @re_gate when a precondition has drifted since plan approval."""

    def __init__(self, message: str, precondition_key: str = "") -> None:
        super().__init__(message)
        self.precondition_key = precondition_key


def guardrail(
    goal: str | None = None,
    risk_tolerance: str = "balanced",
    constraints: dict[str, Any] | None = None,
    dry_run: bool = False,
    on_escalate: Callable[[str, list[Finding]], Any] | None = None,
    planner: PlannerRole | None = None,
    critic: CriticRole | None = None,
    config: LoopConfig | None = None,
) -> Callable[[F], F]:
    """Decorator that gates a function with the PlannerCritic engine.

    Before execution, runs the full plan-critique loop. On approval, calls
    the decorated function. On escalation, raises ``EscalationRequired`` or
    calls ``on_escalate`` if provided.

    Args:
        goal: The goal description. If None, the function's docstring is used.
        risk_tolerance: ``strict``, ``balanced``, or ``permissive``.
        constraints: Optional budget/environment/tools constraints.
        dry_run: If True, shadow mode — the function always executes; the gate
            decision is logged but never blocks.
        on_escalate: Optional callback receiving ``(reason_code, findings)``.
        planner: Optional planner role. Defaults to a simple prompt-based planner.
        critic: Optional critic role.
        config: Optional loop configuration.

    Returns:
        The decorated function.
    """

    def decorator(func: F) -> F:
        goal_text = goal or _get_docstring(func)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            g = _make_goal(goal_text or func.__name__, risk_tolerance, constraints)
            eng = _make_engine(planner, critic, config)
            result = eng.plan(g)

            if result.is_approved:
                logger.info("guardrail: approved — executing %s", func.__name__)
                return func(*args, **kwargs)

            logger.info("guardrail: escalated — %s (reason=%s)", func.__name__, result.reason_code)
            if dry_run:
                logger.warning("guardrail: dry_run — executing despite escalation")
                return func(*args, **kwargs)

            if on_escalate is not None:
                return on_escalate(result.reason_code or "unknown", result.findings)

            raise EscalationRequired(
                f"guardrail blocked {func.__name__}: {result.reason_code}",
                reason_code=result.reason_code or "",
                findings=result.findings,
            )

        return cast(F, wrapper)

    return decorator


def re_gate(
    precondition_key: str = "",
    on_drift: Callable[[str], Any] | None = None,
    ledger: Any = None,
) -> Callable[[F], F]:
    """Decorator that re-verifies a precondition before each function call.

    Args:
        precondition_key: The precondition fact to verify. If empty, uses the
            function name.
        on_drift: Optional callback receiving the precondition key on drift.
        ledger: Optional PreconditionLedger to check against. When provided,
            the function executes normally if the precondition is satisfied.
            When omitted, drift is always assumed (backward-compatible).

    Raises:
        PreconditionDrift: When the precondition is no longer satisfied.
    """
    key = precondition_key

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            pk = key or func.__name__
            logger.info("re_gate: verifying precondition %r for %s", pk, func.__name__)
            if ledger is not None:
                entry = ledger.read(pk)
                if entry is None or not entry.get("satisfied", False):
                    if on_drift is not None:
                        return on_drift(pk)
                    raise PreconditionDrift(
                        f"precondition {pk!r} has drifted — replan required before {func.__name__}",
                        precondition_key=pk,
                    )
                return func(*args, **kwargs)
            # Backward-compatible: when no ledger, always assume drift
            if on_drift is not None:
                return on_drift(pk)
            raise PreconditionDrift(
                f"precondition {pk!r} has drifted — replan required before {func.__name__}",
                precondition_key=pk,
            )

        return cast(F, wrapper)

    return decorator


def escalate(handler: Callable[..., Any] | None = None) -> Callable[..., Any]:
    """Decorator that marks a function as an escalation handler.

    Can be used bare (``@escalate``) or with arguments (unsupported currently).

    Args:
        handler: The escalation handler function.

    Returns:
        The decorated function (identity — no wrapping).
    """
    if handler is None:
        raise TypeError("escalate() requires a handler function")
    return handler


def _get_docstring(func: Callable[..., Any]) -> str | None:
    doc = inspect.getdoc(func)
    if doc:
        return doc.strip()
    return None


def _make_goal(
    description: str,
    risk_tolerance: str,
    constraints: dict[str, Any] | None,
) -> Goal:
    from .schema.goal import Budget
    from .schema.goal import Constraints as GoalConstraints

    rt = RiskTolerance(risk_tolerance)
    c = GoalConstraints()
    if constraints:
        budget = Budget(
            **{
                k: v
                for k, v in constraints.items()
                if k in ("max_tokens", "max_calls", "max_revisions")
            }
        )
        c = GoalConstraints(
            budget=budget,
            **{
                k: v
                for k, v in constraints.items()
                if k not in ("max_tokens", "max_calls", "max_revisions")
            },
        )
    return Goal(
        id=f"guardrail-{hash(description) & 0xFFFFFFFF:08x}",
        description=description,
        risk_tolerance=rt,
        constraints=c,
    )


def _make_engine(
    planner: PlannerRole | None,
    critic: CriticRole | None,
    config: LoopConfig | None,
) -> Engine:
    from .llm.base import Message
    from .llm.structured import StructuredEnforcer
    from .schema.plan import PlanVersion

    if planner is not None and critic is not None:
        return Engine(planner=planner, critic=critic, config=config)

    class _SimplePlanner(PlannerRole):
        _PROMPT = (
            "You are a planner. Reply with ONLY a JSON PlanVersion object. "
            "Each task has: id, description, action, target, risk_class. "
            "High-risk tasks need verification and rollback. "
            "Include dependencies between tasks."
        )

        def __init__(self) -> None:
            self._provider = _get_default_provider()
            self._enforcer = StructuredEnforcer(self._provider)

        def decompose(self, goal: Goal) -> PlanVersion:
            return self._enforcer.complete(
                [
                    Message(role="system", content=self._PROMPT),
                    Message(role="user", content=f"Goal: {goal.description}"),
                ],
                PlanVersion,
            )

        def revise(self, plan: PlanVersion, findings: list[Finding]) -> PlanVersion:
            findings_dump = [f.model_dump() for f in findings]
            return self._enforcer.complete(
                [
                    Message(role="system", content=self._PROMPT + " Revise based on findings."),
                    Message(
                        role="user",
                        content=(f"Plan: {plan.model_dump_json()}\nFindings: {findings_dump}"),
                    ),
                ],
                PlanVersion,
            )

    class _SimpleCritic(CriticRole):
        def __init__(self) -> None:
            self._provider = _get_default_provider()

        def audit(self, plan: PlanVersion, findings: list[Finding]) -> list[Finding]:
            return []

    return Engine(planner=_SimplePlanner(), critic=_SimpleCritic(), config=config)


def _get_default_provider() -> Any:
    from .llm.registry import ProviderRegistry

    try:
        registry = ProviderRegistry.load()
        provider = registry.get_provider("planner")
        if provider is not None:
            return provider
    except Exception:  # noqa: S110 - any registry failure falls through to the fake provider
        pass
    from .llm.base import Completion, Message

    class _FakeProvider:
        name = "fake"
        base_url = ""
        model = "fake"

        def complete(
            self, messages: list[Message], tool_schemas: list[Any] | None = None
        ) -> Completion:
            del messages, tool_schemas
            return Completion(
                content='{"id":"plan","goal_id":"g","version":1,"tasks":[],"dependencies":[]}'
            )

    return _FakeProvider()
