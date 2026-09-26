"""Runtime guards: context, spend-velocity, retry, and circuit breakers (M41)."""

from __future__ import annotations

from hiveplane.guards.breaker import (
    BreakerState,
    CircuitBreaker,
    CircuitBreakerRegistry,
)
from hiveplane.guards.context import (
    ContextAccount,
    ContextBreach,
    ContextBudgetGuard,
)
from hiveplane.guards.manager import GuardLimits, GuardManager
from hiveplane.guards.models import (
    GuardAction,
    GuardEvent,
    GuardKind,
    RetryPolicy,
)
from hiveplane.guards.retry import backoff_delay_ms, run_with_retries
from hiveplane.guards.velocity import (
    SpendVelocityGuard,
    VelocityBreach,
    VelocityEvent,
)

__all__ = [
    "BreakerState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "ContextAccount",
    "ContextBreach",
    "ContextBudgetGuard",
    "GuardAction",
    "GuardEvent",
    "GuardKind",
    "GuardLimits",
    "GuardManager",
    "RetryPolicy",
    "SpendVelocityGuard",
    "VelocityBreach",
    "VelocityEvent",
    "backoff_delay_ms",
    "run_with_retries",
]
