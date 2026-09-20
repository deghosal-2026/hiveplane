"""PydanticAI adapter — agent run failure capture + rule injection (#537).

Duck-typed: no hard ``pydantic_ai`` dependency (import guard).  Provides:

- :func:`watch_run` — decorator for the async function driving ``Agent.run()``:
  a failed run (including tool-call errors / model errors) is captured as a
  trajectory; retries surface as repeated steps.
- :func:`inject_system_rules` — budget-ordered standing rules rendered as a
  compact system-prompt block.
"""

from __future__ import annotations

import functools
import importlib.util
import time
import uuid
from typing import Any, Awaitable, Callable

from cauterule.adapter.inject import inject
from cauterule.capture.failure import detect_failure_class, detect_failure_point
from cauterule.capture.metadata import enrich_trajectory
from cauterule.capture.step import StepCollector
from cauterule.capture.writer import write_trajectory
from cauterule.models.rule import StandingRule
from cauterule.models.trajectory import Trajectory
from cauterule.redaction.engine import redact_trajectory

HAS_PYDANTIC_AI = importlib.util.find_spec("pydantic_ai") is not None


def inject_system_rules(
    task: str,
    *,
    rules: list[StandingRule] | None = None,
    max_rules: int | None = None,
) -> str:
    """Return a compact system-prompt block of matching standing rules.

    Args:
        task: Agent task description to match rules against.
        rules: Standing rules; defaults to the store's active rules.
        max_rules: Optional cap on the number of rules in the block.

    Returns:
        A string like ``"Standing rules (learned from past failures):\\n1. ..."``
        or ``""`` when nothing matches.
    """
    if rules is None:
        from cauterule.store.manager import StoreManager

        rules = StoreManager().list_rules(status="active")
    with inject(task, rules=rules, max_rules=max_rules) as matched:
        lines = [f"{r.id}: when {r.when.trigger} do {r.do.directive}" for r in matched]
    if not lines:
        return ""
    return "Standing rules (learned from past failures):\n" + "\n".join(
        f"{i + 1}. {line}" for i, line in enumerate(lines)
    )


def _capture(
    collector: StepCollector,
    task: str,
    *,
    error: str | None,
    base_dir: str,
) -> Trajectory:
    steps = collector.steps()
    success = error is None
    traj = Trajectory(
        id=f"T-{uuid.uuid4().hex[:8]}",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        task=task,
        steps=steps,
        success=success,
        failure_point=detect_failure_point(steps),
        failure_class=detect_failure_class(steps),
    )
    traj = enrich_trajectory(traj)
    traj = redact_trajectory(traj)
    write_trajectory(traj, base_dir=base_dir)
    return traj


def watch_run(
    func: Callable[..., Awaitable[Any]] | None = None,
    *,
    task: str | None = None,
    base_dir: str = "trajectories",
    capture_success: bool = True,
) -> Callable[..., Any]:
    """Capture a PydanticAI ``Agent.run()`` as a trajectory (#537).

    Async-first (PydanticAI is async-native); a sync callable is also
    supported as a fallback.  A single trajectory is written per run — not
    per streamed chunk — with the run's outcome (success or error).

    Args:
        func: The async (or sync) function that drives ``agent.run(...)``.
        task: Agent task description (defaults to the function name).
        base_dir: Trajectory output directory.
        capture_success: When False, only failed runs are captured.
    """

    def decorator(inner: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        task_desc = task or inner.__name__

        if _is_coroutine(inner):

            @functools.wraps(inner)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                collector = StepCollector()
                try:
                    result = await inner(*args, **kwargs)
                    if not capture_success:
                        return result
                    collector.add(
                        tool=inner.__name__,
                        input=_run_input_repr(args, kwargs),
                        output=str(result)[:2000],
                    )
                    _capture(collector, task_desc, error=None, base_dir=base_dir)
                    return result
                except Exception as exc:
                    collector.add(
                        tool=inner.__name__,
                        input=_run_input_repr(args, kwargs),
                        error=str(exc),
                    )
                    _capture(collector, task_desc, error=str(exc), base_dir=base_dir)
                    raise

            return wrapper

        @functools.wraps(inner)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            collector = StepCollector()
            try:
                result = inner(*args, **kwargs)
                if not capture_success:
                    return result
                collector.add(
                    tool=inner.__name__,
                    input=_run_input_repr(args, kwargs),
                    output=str(result)[:2000],
                )
                _capture(collector, task_desc, error=None, base_dir=base_dir)
                return result
            except Exception as exc:
                collector.add(
                    tool=inner.__name__, input=_run_input_repr(args, kwargs), error=str(exc)
                )
                _capture(collector, task_desc, error=str(exc), base_dir=base_dir)
                raise

        return sync_wrapper

    if func is not None and callable(func):
        return decorator(func)
    return decorator


def _is_coroutine(func: Callable[..., Any]) -> bool:
    import inspect

    return inspect.iscoroutinefunction(func)


_DEFAULT_REDACT_KEYS = ("api_key", "token", "secret", "password")
_REDACTED = "[REDACTED]"
_SECRET_PREFIXES = (
    "sk-",
    "sk_live_",
    "rk_live_",
    "pk_live_",
    "ghp_",
    "github_pat_",
    "xoxb-",
    "AKIA",
    "ASIA",
    "eyJ",
)


def _is_secret(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or len(s) < 12:
        return False
    return any(s.startswith(p) for p in _SECRET_PREFIXES) or (
        len(s) >= 20 and sum(c.isalnum() or c in "=+-/" for c in s) / len(s) > 0.8
    )


def _redact_value(value: Any) -> Any:
    return _REDACTED if _is_secret(value) else value


def _redact_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (_REDACTED if any(rk in k.lower() for rk in _DEFAULT_REDACT_KEYS) else _redact_value(v))
        for k, v in kwargs.items()
    }


def _run_input_repr(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Redacted repr of a run's args+kwargs (code-review: positional args and
    secret-prefix values are redacted too, not just key-named kwargs)."""
    return str({"args": tuple(_redact_value(a) for a in args), "kwargs": _redact_kwargs(kwargs)})[
        :2000
    ]
