"""Per-tool and per-workload circuit breakers with a half-open probe (M41-05)."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class BreakerState(StrEnum):
    """Circuit-breaker state machine states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker(BaseModel):
    """The recorded state of one breaker."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["tool", "workload"]
    scope_id: str = Field(min_length=1)
    state: BreakerState
    failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    opened_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class _Breaker:
    def __init__(self) -> None:
        self.state = BreakerState.CLOSED
        self.outcomes: deque[bool] = deque()
        self.opened_at: datetime | None = None
        self.failure_rate = 0.0


class CircuitBreakerRegistry:
    """Trips, half-opens, and recovers breakers keyed by (scope, scope_id)."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        failure_threshold: float = 0.5,
        min_calls: int = 5,
        open_for_seconds: int = 30,
        window: int = 20,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._failure_threshold = failure_threshold
        self._min_calls = min_calls
        self._open_for = timedelta(seconds=open_for_seconds)
        self._window = window
        self._breakers: dict[tuple[str, str], _Breaker] = {}

    def _breaker(self, scope: str, scope_id: str) -> _Breaker:
        return self._breakers.setdefault((scope, scope_id), _Breaker())

    def state(self, scope: str, scope_id: str) -> BreakerState:
        """Return the current breaker state."""
        return self._breaker(scope, scope_id).state

    def allow(self, scope: str, scope_id: str) -> bool:
        """Return True when a call may proceed (closed, or one half-open probe)."""
        breaker = self._breaker(scope, scope_id)
        if breaker.state is BreakerState.CLOSED:
            return True
        if breaker.state is BreakerState.OPEN:
            opened = breaker.opened_at or self._clock()
            if self._clock() - opened >= self._open_for:
                breaker.state = BreakerState.HALF_OPEN
                return True
            return False
        return False

    def record(self, scope: str, scope_id: str, *, success: bool) -> CircuitBreaker:
        """Record an outcome and update the breaker state machine."""
        breaker = self._breaker(scope, scope_id)
        now = self._clock()
        if breaker.state is BreakerState.HALF_OPEN:
            breaker.state = BreakerState.CLOSED if success else BreakerState.OPEN
            breaker.outcomes.clear()
            breaker.failure_rate = 0.0 if success else 1.0
            breaker.opened_at = None if success else now
            return self._snapshot(scope, scope_id, breaker, now)
        breaker.outcomes.append(success)
        while len(breaker.outcomes) > self._window:
            breaker.outcomes.popleft()
        failures = sum(1 for outcome in breaker.outcomes if not outcome)
        breaker.failure_rate = failures / len(breaker.outcomes)
        if (
            len(breaker.outcomes) >= self._min_calls
            and breaker.failure_rate >= self._failure_threshold
        ):
            breaker.state = BreakerState.OPEN
            breaker.opened_at = now
        return self._snapshot(scope, scope_id, breaker, now)

    def snapshot(self, scope: str, scope_id: str) -> CircuitBreaker:
        """Return the breaker state without mutating it."""
        return self._snapshot(scope, scope_id, self._breaker(scope, scope_id), self._clock())

    def _snapshot(
        self, scope: str, scope_id: str, breaker: _Breaker, now: datetime
    ) -> CircuitBreaker:
        return CircuitBreaker(
            scope=scope,  # type: ignore[arg-type]
            scope_id=scope_id,
            state=breaker.state,
            failure_rate=breaker.failure_rate,
            opened_at=breaker.opened_at,
            updated_at=now,
        )
