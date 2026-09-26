"""Retry policies with exponential backoff and jitter (M41-04)."""

from __future__ import annotations

from collections.abc import Callable

from hiveplane.guards.models import RetryPolicy


def backoff_delay_ms(
    policy: RetryPolicy, attempt: int, *, rand: Callable[[], float]
) -> int:
    """Return the delay before ``attempt`` (0-based) with optional full jitter."""
    capped: int = min(policy.max_ms, policy.base_ms * (2**attempt))
    if policy.jitter == "full":
        jittered: float = rand() * float(capped)
        return int(jittered)
    return capped


def run_with_retries[T](
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[int], None],
    rand: Callable[[], float],
) -> tuple[T, int]:
    """Run ``fn`` with the retry policy; return (result, attempts).

    Re-raises the last error when all attempts are exhausted. ``sleep`` and
    ``rand`` are injected so tests are deterministic.
    """
    last_error: Exception | None = None
    for attempt in range(policy.max_attempts):
        try:
            return fn(), attempt + 1
        except Exception as exc:
            last_error = exc
            if attempt < policy.max_attempts - 1:
                sleep(backoff_delay_ms(policy, attempt, rand=rand))
    assert last_error is not None
    raise last_error
