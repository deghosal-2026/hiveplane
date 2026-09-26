"""Tests for runtime guards: context, spend-velocity, retry, and breakers (M41)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.guards.breaker import (
    BreakerState,
    CircuitBreakerRegistry,
)
from hiveplane.guards.context import ContextBudgetGuard
from hiveplane.guards.manager import GuardManager
from hiveplane.guards.models import GuardAction, RetryPolicy
from hiveplane.guards.retry import backoff_delay_ms, run_with_retries
from hiveplane.guards.velocity import SpendVelocityGuard

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Context-window budget (M41-01, M41-02)
# --------------------------------------------------------------------------- #
def test_context_accounting_accumulates_per_run() -> None:
    guard = ContextBudgetGuard(clock=lambda: _NOW)

    first = guard.record("run-1", step=0, model_identity="m1", input_tokens=100, output_tokens=50)
    second = guard.record("run-1", step=1, model_identity="m1", input_tokens=80, output_tokens=20)
    guard.record("run-2", step=0, model_identity="m1", input_tokens=10, output_tokens=0)

    assert first.context_total == 150
    assert second.context_total == 250
    assert guard.total("run-1") == 250
    assert guard.total("run-2") == 10


def test_context_breach_reports_accounting() -> None:
    guard = ContextBudgetGuard(clock=lambda: _NOW)
    guard.record("run-1", step=0, model_identity="m1", input_tokens=150, output_tokens=50)

    breach = guard.check("run-1", limit=180, warn_at=0.8)

    assert breach is not None
    assert breach.used == 200
    assert breach.limit == 180
    assert breach.step == 0
    assert breach.model_identity == "m1"
    assert breach.rule_id == "guard.context"


def test_context_within_limit_has_no_breach() -> None:
    guard = ContextBudgetGuard(clock=lambda: _NOW)
    guard.record("run-1", step=0, model_identity="m1", input_tokens=100, output_tokens=50)

    assert guard.check("run-1", limit=1000, warn_at=0.8) is None
    assert guard.is_warning("run-1", limit=1000, warn_at=0.8) is False


def test_context_warning_threshold() -> None:
    guard = ContextBudgetGuard(clock=lambda: _NOW)
    guard.record("run-1", step=0, model_identity="m1", input_tokens=850, output_tokens=0)

    assert guard.is_warning("run-1", limit=1000, warn_at=0.8) is True


def test_context_accounts_are_listed_in_order() -> None:
    guard = ContextBudgetGuard(clock=lambda: _NOW)
    guard.record("run-1", step=0, model_identity="m1", input_tokens=1, output_tokens=1)
    guard.record("run-1", step=1, model_identity="m1", input_tokens=2, output_tokens=2)

    assert [account.step for account in guard.accounts("run-1")] == [0, 1]


# --------------------------------------------------------------------------- #
# Spend-velocity guard (M41-03)
# --------------------------------------------------------------------------- #
def test_velocity_within_limit_has_no_breach() -> None:
    guard = SpendVelocityGuard(clock=lambda: _NOW)
    guard.record("repo-agent", 0.10)

    assert (
        guard.check("repo-agent", window_seconds=300, limit_usd=1.0) is None
    )


def test_velocity_breach_reports_rate_and_exhaustion() -> None:
    guard = SpendVelocityGuard(clock=lambda: _NOW)
    guard.record("repo-agent", 0.80)
    guard.record("repo-agent", 0.80)

    breach = guard.check(
        "repo-agent", window_seconds=300, limit_usd=1.0, budget_remaining_usd=0.40
    )

    assert breach is not None
    assert breach.spent_usd == pytest.approx(1.60)
    assert breach.limit_usd == 1.0
    assert breach.rule_id == "guard.velocity"
    assert breach.projected_exhaustion_seconds is not None


def test_velocity_window_excludes_old_spend() -> None:
    now = {"t": _NOW}
    guard = SpendVelocityGuard(clock=lambda: now["t"])
    guard.record("repo-agent", 5.0)
    now["t"] = _NOW + timedelta(minutes=10)
    guard.record("repo-agent", 0.20)

    breach = guard.check("repo-agent", window_seconds=300, limit_usd=1.0)

    assert breach is None


def test_velocity_multiplier_trips_against_a_baseline() -> None:
    guard = SpendVelocityGuard(clock=lambda: _NOW)
    guard.record("repo-agent", 0.60)

    breach = guard.check(
        "repo-agent",
        window_seconds=300,
        limit_usd=100.0,
        multiplier=5.0,
        baseline_spent_usd=0.05,
    )

    assert breach is not None


# --------------------------------------------------------------------------- #
# Retry policies (M41-04)
# --------------------------------------------------------------------------- #
def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(max_attempts=5, base_ms=500, max_ms=4000, jitter="none")

    delays = [backoff_delay_ms(policy, attempt, rand=lambda: 0.5) for attempt in range(5)]

    assert delays == [500, 1000, 2000, 4000, 4000]


def test_full_jitter_stays_within_the_capped_delay() -> None:
    policy = RetryPolicy(max_attempts=4, base_ms=500, max_ms=4000, jitter="full")

    for attempt in range(4):
        delay = backoff_delay_ms(policy, attempt, rand=lambda: 0.5)
        assert 0 <= delay <= min(4000, 500 * (2**attempt))


def test_run_with_retries_succeeds_after_a_transient_failure() -> None:
    policy = RetryPolicy(max_attempts=3, base_ms=10, max_ms=100, jitter="none")
    calls = {"n": 0}
    slept: list[int] = []

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    result, attempts = run_with_retries(
        flaky, policy, sleep=slept.append, rand=lambda: 0.0
    )

    assert result == "ok"
    assert attempts == 2
    assert slept == [10]


def test_run_with_retries_stops_at_max_attempts() -> None:
    policy = RetryPolicy(max_attempts=3, base_ms=1, max_ms=10, jitter="none")
    slept: list[int] = []

    def always_fails() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_with_retries(always_fails, policy, sleep=slept.append, rand=lambda: 0.0)

    assert len(slept) == 2  # two waits between three attempts


# --------------------------------------------------------------------------- #
# Circuit breakers (M41-05, M41-06)
# --------------------------------------------------------------------------- #
def _breaker(*, clock: Callable[[], datetime] = lambda: _NOW) -> CircuitBreakerRegistry:
    return CircuitBreakerRegistry(
        clock=clock, failure_threshold=0.5, min_calls=4, open_for_seconds=30
    )


def test_breaker_starts_closed_and_allows() -> None:
    breaker = _breaker()

    assert breaker.state("tool", "mcp.t.read") is BreakerState.CLOSED
    assert breaker.allow("tool", "mcp.t.read") is True


def test_breaker_trips_after_the_failure_threshold() -> None:
    breaker = _breaker()
    for _ in range(2):
        breaker.record("tool", "mcp.t.read", success=True)
    for _ in range(2):
        breaker.record("tool", "mcp.t.read", success=False)

    assert breaker.state("tool", "mcp.t.read") is BreakerState.OPEN
    assert breaker.allow("tool", "mcp.t.read") is False


def test_breaker_half_opens_after_open_for_and_recovers() -> None:
    now = {"t": _NOW}
    breaker = _breaker(clock=lambda: now["t"])
    for _ in range(4):
        breaker.record("tool", "mcp.t.read", success=False)
    assert breaker.allow("tool", "mcp.t.read") is False

    now["t"] = _NOW + timedelta(seconds=31)
    assert breaker.allow("tool", "mcp.t.read") is True
    assert breaker.state("tool", "mcp.t.read") is BreakerState.HALF_OPEN

    breaker.record("tool", "mcp.t.read", success=True)
    assert breaker.state("tool", "mcp.t.read") is BreakerState.CLOSED


def test_breaker_reopens_when_the_probe_fails() -> None:
    now = {"t": _NOW}
    breaker = _breaker(clock=lambda: now["t"])
    for _ in range(4):
        breaker.record("tool", "mcp.t.read", success=False)
    now["t"] = _NOW + timedelta(seconds=31)
    assert breaker.allow("tool", "mcp.t.read") is True

    breaker.record("tool", "mcp.t.read", success=False)

    assert breaker.state("tool", "mcp.t.read") is BreakerState.OPEN
    assert breaker.allow("tool", "mcp.t.read") is False


def test_breaker_is_scoped_independently() -> None:
    breaker = _breaker()
    for _ in range(4):
        breaker.record("tool", "bad", success=False)

    assert breaker.state("tool", "bad") is BreakerState.OPEN
    assert breaker.state("tool", "good") is BreakerState.CLOSED


def test_guard_action_values() -> None:
    assert GuardAction.PAUSE.value == "pause"
    assert GuardAction.DENY.value == "deny"
    assert GuardAction.THROTTLE.value == "throttle"


# --------------------------------------------------------------------------- #
# Guard manager + execution integration (M41-01/02/03/06/07)
# --------------------------------------------------------------------------- #
def _manager(
    *, context_tokens: int | None = 100, velocity_limit: float | None = None
) -> GuardManager:
    from hiveplane.guards.context import ContextBudgetGuard
    from hiveplane.guards.manager import GuardLimits, GuardManager
    from hiveplane.guards.velocity import SpendVelocityGuard

    limits = GuardLimits(
        context_tokens=context_tokens, velocity_limit_usd=velocity_limit
    )
    return GuardManager(
        ContextBudgetGuard(clock=lambda: _NOW),
        SpendVelocityGuard(clock=lambda: _NOW),
        limit_lookup=lambda workload: limits,
        clock=lambda: _NOW,
        id_factory=lambda: "guard-1",
    )


def _usage(
    run_id: str, *, input_tokens: int = 0, output_tokens: int = 0, cost: float = 0.0
) -> UsageReport:
    from hiveplane.core.usage import UsageReport

    return UsageReport(
        run_id=run_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=0,
        cost_usd=cost,
        timestamp=_NOW,
    )


def test_manager_returns_a_context_breach_event() -> None:
    manager = _manager(context_tokens=100)

    from hiveplane.core.run import AdmissionContext, Run, RunState

    run = Run(
        id="run-1",
        workload_id="repo-agent",
        caller="cli",
        state=RunState.RUNNING,
        context=AdmissionContext.PRODUCTION,
        created_at=_NOW,
        updated_at=_NOW,
    )
    event = manager.on_usage(
        run, _usage("run-1", input_tokens=150, output_tokens=50)
    )

    assert event is not None
    assert event.guard.value == "context"
    assert event.action.value == "pause"
    assert event.rule_id == "guard.context"


def test_manager_returns_a_velocity_breach_event() -> None:
    manager = _manager(context_tokens=None, velocity_limit=0.5)
    from hiveplane.core.run import AdmissionContext, Run, RunState

    run = Run(
        id="run-1",
        workload_id="repo-agent",
        caller="cli",
        state=RunState.RUNNING,
        context=AdmissionContext.PRODUCTION,
        created_at=_NOW,
        updated_at=_NOW,
    )
    event = manager.on_usage(run, _usage("run-1", cost=0.9))

    assert event is not None
    assert event.guard.value == "velocity"
    assert event.rule_id == "guard.velocity"


def test_context_breach_pauses_the_run_cleanly(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.core.run import AdmissionContext, RunState
    from test_execution_service import _service

    service, _, _, workload = _service(make_manifest)
    service.attach_guards(_manager(context_tokens=100))
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler")

    paused = service.record_usage(run.id, _usage(run.id, input_tokens=150, output_tokens=50))

    assert paused.state is RunState.PAUSED
    assert any(event.type.value == "guard" for event in service.events(run.id))


def test_velocity_breach_pauses_the_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.core.run import AdmissionContext, RunState
    from test_execution_service import _service

    service, _, _, workload = _service(make_manifest)
    service.attach_guards(_manager(context_tokens=None, velocity_limit=0.5))
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler")

    paused = service.record_usage(run.id, _usage(run.id, cost=0.9))

    assert paused.state is RunState.PAUSED


def test_gateway_denies_an_open_circuit_breaker(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from types import SimpleNamespace

    from hiveplane.execution.tools import ToolCallOutcome, ToolCallRequest, ToolGateway
    from hiveplane.policy.engine import PolicyEngine
    from hiveplane.policy.packs import InMemoryPolicyPackStore
    from test_tool_gateway import _run as _gw_run
    from test_tool_gateway import _Runs, _workload

    workload = _workload(make_manifest)
    breaker = _breaker()
    for _ in range(4):
        breaker.record("tool", "mcp.t.read", success=False)
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(manifest=workload))
    gateway = ToolGateway(
        registry,  # type: ignore[arg-type]
        PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _NOW),
        _Runs(_gw_run()),
        breaker=breaker,
        clock=lambda: _NOW,
    )

    result = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.read"))

    assert result.outcome is ToolCallOutcome.DENIED
    assert result.rule == "circuit_open"
