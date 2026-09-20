"""@cauterule.watch decorator (#538).

GA-grade custom-loop capture: sync, async, generator, and async-generator
aware.  On each call it captures a trajectory step (with configurable kwargs
redaction *before* serialization), fields it via the failure/enrich/redact
pipeline, and writes it to ``trajectories/YYYY-MM-DD/``.

Supports ``@watch``, ``@watch(base_dir=..., capture_success=...)``, and
``@watch(...)`` applied to sync / async / generator / async-generator
functions.
"""

from __future__ import annotations

import functools
import inspect
import time
import uuid
from typing import Any, Callable

from cauterule.capture.failure import detect_failure_class, detect_failure_point
from cauterule.capture.metadata import enrich_trajectory
from cauterule.capture.step import StepCollector
from cauterule.capture.writer import write_trajectory
from cauterule.models.trajectory import Trajectory
from cauterule.redaction.engine import redact_trajectory

# Default kwargs keys dropped before serializing step input (#538).  Users can
# override via ``redact_keys=``.
DEFAULT_REDACT_KEYS: frozenset[str] = frozenset(
    {"api_key", "token", "secret", "password", "authorization", "auth", "key"}
)

_REDACTED_PLACEHOLDER = "[REDACTED]"

# Known secret-token prefixes the key-name check alone can miss (e.g. a
# positional arg carrying ``"sk-secret-abcdef"``) (code-review).
_SECRET_PREFIXES: tuple[str, ...] = (
    "sk-",
    "sk_live_",
    "rk_live_",
    "pk_live_",
    "ghp_",
    "github_pat_",
    "xoxb-",
    "xoxp-",
    "AKIA",
    "ASIA",
    "eyJ",  # JWT header
)


def _looks_like_secret(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or len(s) < 12:
        return False
    return any(s.startswith(p) for p in _SECRET_PREFIXES) or (
        len(s) >= 20 and sum(c.isalnum() or c in "=+-/" for c in s) / len(s) > 0.8
    )


def _redact_value(value: Any) -> Any:
    """Return *value* placeholdered if it looks like a secret."""
    return _REDACTED_PLACEHOLDER if _looks_like_secret(value) else value


def _redact_kwargs(kwargs: dict[str, Any], redact_keys: frozenset[str]) -> dict[str, Any]:
    """Return a copy of *kwargs* with sensitive values placeholdered."""
    cleaned: dict[str, Any] = {}
    for k, v in kwargs.items():
        if any(k.lower() == rk.lower() or rk.lower() in k.lower() for rk in redact_keys):
            cleaned[k] = _REDACTED_PLACEHOLDER
        else:
            cleaned[k] = _redact_value(v)
    return cleaned


def _input_repr(args: tuple[Any, ...], kwargs: dict[str, Any], redact_keys: frozenset[str]) -> str:
    clean_kwargs = _redact_kwargs(kwargs, redact_keys)
    clean_args = tuple(_redact_value(a) for a in args)
    return str({"args": clean_args, "kwargs": clean_kwargs})[:2000]


def _build_trajectory(
    tool: str,
    task: str,
    step_input: str,
    *,
    success: bool,
    output: str | None = None,
    error: str | None = None,
    duration: float,
) -> Trajectory:
    collector = StepCollector()
    state: dict[str, Any] = {"duration": duration}
    if success:
        collector.add(tool=tool, input=step_input, output=output or "", state=state)
    else:
        collector.add(tool=tool, input=step_input, error=error or "", state=state)
    steps = collector.steps()
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
    return redact_trajectory(traj)


def watch(
    func: Callable[..., Any] | None = None,
    *,
    base_dir: str = "trajectories",
    capture_success: bool = True,
    redact_keys: frozenset[str] = DEFAULT_REDACT_KEYS,
) -> Callable[..., Any]:
    """Decorator to capture a trajectory for an agent function (#538).

    Args:
        func: Function to wrap (optional; allows ``@watch`` or ``@watch(...)``).
        base_dir: Base directory for trajectory files.
        capture_success: If ``False``, only failures are captured.
        redact_keys: Kwarg keys placeholdered before step input is stringified.
    """

    def decorator(inner: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.isasyncgenfunction(inner):
            return _wrap_async_gen(inner, base_dir, capture_success, redact_keys)
        if inspect.isgeneratorfunction(inner):
            return _wrap_gen(inner, base_dir, capture_success, redact_keys)
        if inspect.iscoroutinefunction(inner):
            return _wrap_async(inner, base_dir, capture_success, redact_keys)
        return _wrap_sync(inner, base_dir, capture_success, redact_keys)

    if func is not None and callable(func):
        return decorator(func)
    return decorator


def _wrap_sync(
    inner: Callable[..., Any], base_dir: str, capture_success: bool, redact_keys: frozenset[str]
) -> Callable[..., Any]:
    @functools.wraps(inner)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.time()
        try:
            result = inner(*args, **kwargs)
            if not capture_success:
                return result
            traj = _build_trajectory(
                inner.__name__,
                inner.__name__,
                _input_repr(args, kwargs, redact_keys),
                success=True,
                output=str(result),
                duration=time.time() - start,
            )
            write_trajectory(traj, base_dir=base_dir)
            return result
        except Exception as exc:
            traj = _build_trajectory(
                inner.__name__,
                inner.__name__,
                _input_repr(args, kwargs, redact_keys),
                success=False,
                error=str(exc),
                duration=time.time() - start,
            )
            write_trajectory(traj, base_dir=base_dir)
            raise

    return wrapper


def _wrap_async(
    inner: Callable[..., Any], base_dir: str, capture_success: bool, redact_keys: frozenset[str]
) -> Callable[..., Any]:
    @functools.wraps(inner)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.time()
        try:
            result = await inner(*args, **kwargs)
            if not capture_success:
                return result
            traj = _build_trajectory(
                inner.__name__,
                inner.__name__,
                _input_repr(args, kwargs, redact_keys),
                success=True,
                output=str(result),
                duration=time.time() - start,
            )
            write_trajectory(traj, base_dir=base_dir)
            return result
        except Exception as exc:
            traj = _build_trajectory(
                inner.__name__,
                inner.__name__,
                _input_repr(args, kwargs, redact_keys),
                success=False,
                error=str(exc),
                duration=time.time() - start,
            )
            write_trajectory(traj, base_dir=base_dir)
            raise

    return wrapper


def _wrap_gen(
    inner: Callable[..., Any], base_dir: str, capture_success: bool, redact_keys: frozenset[str]
) -> Callable[..., Any]:
    """Wrap a sync generator: capture the first yielded step (if capturing
    success), then record a final failure step on error — first+error for
    unbounded streams (code-review: respects capture_success; does not buffer
    the whole stream)."""

    @functools.wraps(inner)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        step_input = _input_repr(args, kwargs, redact_keys)
        first = True
        try:
            gen = inner(*args, **kwargs)
            for item in gen:
                if first and capture_success:
                    # record an initial step so a long stream has context
                    traj = _build_trajectory(
                        inner.__name__,
                        inner.__name__,
                        step_input,
                        success=True,
                        output=str(item),
                        duration=0.0,
                    )
                    write_trajectory(traj, base_dir=base_dir)
                first = False
                yield item
            if first and capture_success:
                traj = _build_trajectory(
                    inner.__name__,
                    inner.__name__,
                    step_input,
                    success=True,
                    output="<empty generator>",
                    duration=0.0,
                )
                write_trajectory(traj, base_dir=base_dir)
        except Exception as exc:
            traj = _build_trajectory(
                inner.__name__,
                inner.__name__,
                step_input,
                success=False,
                error=str(exc),
                duration=0.0,
            )
            write_trajectory(traj, base_dir=base_dir)
            raise

    return wrapper


def _wrap_async_gen(
    inner: Callable[..., Any], base_dir: str, capture_success: bool, redact_keys: frozenset[str]
) -> Callable[..., Any]:
    """Wrap an async generator: record the first yielded item (if capturing
    success), and a final failure step on error."""

    @functools.wraps(inner)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        step_input = _input_repr(args, kwargs, redact_keys)
        first = True
        try:
            gen = inner(*args, **kwargs)
            async for item in gen:
                if first and capture_success:
                    traj = _build_trajectory(
                        inner.__name__,
                        inner.__name__,
                        step_input,
                        success=True,
                        output=str(item),
                        duration=0.0,
                    )
                    write_trajectory(traj, base_dir=base_dir)
                first = False
                yield item
            if first and capture_success:
                traj = _build_trajectory(
                    inner.__name__,
                    inner.__name__,
                    step_input,
                    success=True,
                    output="<empty async generator>",
                    duration=0.0,
                )
                write_trajectory(traj, base_dir=base_dir)
        except Exception as exc:
            traj = _build_trajectory(
                inner.__name__,
                inner.__name__,
                step_input,
                success=False,
                error=str(exc),
                duration=0.0,
            )
            write_trajectory(traj, base_dir=base_dir)
            raise

    return wrapper
