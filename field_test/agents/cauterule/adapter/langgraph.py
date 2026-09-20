"""LangGraph adapter — node failure capture + rule injection (#534).

Duck-typed: no hard ``langgraph`` dependency (import guard via
``importlib.util.find_spec``).  Provides:

- :func:`inject_rules` — prepend matching standing rules to a node's prompt
  context (real matcher + budget from :mod:`cauterule.injection`).
- :func:`capture_node_error` — record a failed node invocation as a
  trajectory step (state snapshots pass through redaction).
- :func:`langgraph_node` — decorator wrapping a node callable with capture
  + injection.
"""

from __future__ import annotations

import functools
import importlib.util
import time
import uuid
from typing import Any, Callable, cast

from cauterule.adapter.inject import inject
from cauterule.capture.failure import detect_failure_class, detect_failure_point
from cauterule.capture.metadata import enrich_trajectory
from cauterule.capture.step import StepCollector
from cauterule.capture.writer import write_trajectory
from cauterule.models.rule import StandingRule
from cauterule.models.trajectory import Trajectory
from cauterule.redaction.engine import redact_trajectory

# Set True when the optional langgraph package is importable.
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

_RULES_KEY = "cauterule_rules"


def inject_rules(
    state: dict[str, Any],
    task: str | None = None,
    rules: list[StandingRule] | None = None,
    *,
    formatter: Callable[[list[StandingRule]], str] | None = None,
) -> dict[str, Any]:
    """Return a copy of *state* with matching standing rules injected.

    Uses the real matcher (budget-ordered).  The rendered rules are stored
    under ``state["cauterule_rules"]`` as a list of text blocks the node
    prepends to its prompt.

    Args:
        state: LangGraph state dict to copy.
        task: Task/step description to match rules against.
        rules: Standing rules to filter (defaults to store rules).
        formatter: Optional callback rendering matched rules to text.

    Returns:
        A shallow copy of *state* augmented with the injected rules.
    """
    if task is None:
        task = str(state.get("task", state.get("messages", "")))
    new_state = dict(state)
    if rules is None:
        rules = _load_store_rules()
    with inject(task, rules=rules) as matched:
        rendered: list[str] = []
        for r in matched:
            rendered.append(f"{r.id}: when {r.when.trigger} do {r.do.directive}")
    if not rendered:
        return new_state
    if formatter is not None:
        new_state[_RULES_KEY] = formatter(matched)
    else:
        new_state[_RULES_KEY] = rendered
    return new_state


def _load_store_rules() -> list[StandingRule]:
    from cauterule.store.manager import StoreManager

    return StoreManager().list_rules(status="active")


def _redacted_repr(obj: Any) -> str:
    """Redact secrets in a dict repr (positional state snapshots) (#code-review)."""
    from cauterule.adapter.pydanticai import _run_input_repr

    if isinstance(obj, dict):
        return _run_input_repr((), obj)
    return _run_input_repr((obj,), {})


def capture_node_error(
    node_name: str,
    state_in: dict[str, Any],
    state_out: dict[str, Any],
    error: Exception,
    *,
    base_dir: str = "trajectories",
) -> Trajectory:
    """Capture a failed node run as a trajectory (input + error steps).

    Args:
        node_name: Name of the node that failed.
        state_in: Graph state snapshot before the node ran.
        state_out: Graph state after the failure (may be partial).
        error: The exception that failed the node.
        base_dir: Trajectory output directory.

    Returns:
        The written :class:`Trajectory`.
    """
    collector = StepCollector()
    collector.add(tool=node_name, input=_redacted_repr(state_in))
    collector.add(tool=node_name, input=_redacted_repr(state_out), error=str(error))
    steps = collector.steps()
    traj = Trajectory(
        id=f"T-{uuid.uuid4().hex[:8]}",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        task=node_name,
        steps=steps,
        success=False,
        failure_point=detect_failure_point(steps),
        failure_class=detect_failure_class(steps),
    )
    traj = enrich_trajectory(traj)
    traj = redact_trajectory(traj)
    write_trajectory(traj, base_dir=base_dir)
    return traj


def langgraph_node(
    func: Callable[..., Any] | None = None,
    *,
    task: str | None = None,
    base_dir: str = "trajectories",
) -> Callable[..., Any]:
    """Decorator wrapping a LangGraph node callable with capture + injection.

    On success injects matching rules into the returned state; on exception
    captures the node failure as a trajectory and re-raises.

    Args:
        func: The node callable ``(state) -> state``.
        task: Optional task description; defaults to the node name.
        base_dir: Trajectory output directory.
    """

    def decorator(inner: Callable[..., Any]) -> Callable[..., Any]:
        node_name = inner.__name__

        @functools.wraps(inner)
        def wrapper(state: dict[str, Any]) -> dict[str, Any]:
            task_desc = task or node_name
            state_in = state
            try:
                state = inject_rules(state, task=task_desc)
                return cast(dict[str, Any], inner(state))
            except Exception as exc:
                capture_node_error(node_name, state_in, state, exc, base_dir=base_dir)
                raise

        return wrapper

    if func is not None and callable(func):
        return decorator(func)
    return decorator
