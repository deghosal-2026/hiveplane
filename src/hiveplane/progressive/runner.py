"""The production shadow runner: executes a candidate via the run service (M37-01).

The runner submits the candidate in the isolated ``sandbox`` context with
``read_only=True`` and ``shadow_of`` set, so the resulting run cannot cause side
effects and is never delivered to a fan-out channel. It then reports whatever
evidence the run has produced.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable

from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.execution.service import RunService
from hiveplane.progressive.errors import ShadowRunTimeoutError
from hiveplane.progressive.models import ShadowOutcome, ShadowStatus
from hiveplane.tenancy.context import TenantContext, context_for_run

#: Automated shadow work is attributed to this actor.
ACTOR = "progressive-delivery"

_TERMINAL_RUN_STATES = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)


class RunShadowRunner:
    """A :class:`~hiveplane.progressive.shadow.ShadowRunner` over the run service."""

    def __init__(
        self,
        runs: RunService,
        *,
        timeout_s: float = 300.0,
        poll_interval_s: float = 0.05,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runs = runs
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s
        self._sleep = sleep
        self._monotonic = monotonic

    def run(
        self,
        production_run: Run,
        *,
        candidate_workload_id: str,
        candidate_version: int | None,
        budget_id: str,
    ) -> ShadowOutcome:
        ctx = run_context(production_run)
        shadow_run = self._runs.submit(
            workload=candidate_workload_id,
            caller=ACTOR,
            context=AdmissionContext.SANDBOX,
            task=production_run.task,
            model_identity=production_run.model_identity,
            shadow_of=production_run.id,
            read_only=True,
            ctx=ctx,
        )
        with contextlib.suppress(Exception):
            self._runs.start(shadow_run.id, actor=ACTOR, ctx=ctx)
        latest = self._wait_for_terminal(shadow_run.id, ctx)
        events = self._runs.events(shadow_run.id, ctx=ctx)
        return ShadowOutcome(
            status=(
                ShadowStatus.FAILED
                if latest.state in (RunState.FAILED, RunState.CANCELLED)
                else ShadowStatus.COMPLETED
            ),
            result=latest.result,
            cost_usd=latest.cost_usd,
            latency_ms=_latency_ms(latest),
            tool_calls=[
                {"event": event.type.value, "detail": event.detail}
                for event in events
                if event.type.value == "tool_call"
            ],
            policy_decisions=[
                {"event": event.type.value, "detail": event.detail}
                for event in events
                if event.type.value == "policy_decision"
            ],
            failure_reason=latest.failure_reason,
        )

    def _wait_for_terminal(self, run_id: str, ctx: TenantContext) -> Run:
        """Block until the shadow run reaches a terminal state, or time out."""
        latest = self._runs.get(run_id, ctx=ctx)
        deadline = self._monotonic() + self._timeout_s
        while latest.state not in _TERMINAL_RUN_STATES:
            if self._monotonic() >= deadline:
                raise ShadowRunTimeoutError(run_id)
            self._sleep(self._poll_interval_s)
            latest = self._runs.get(run_id, ctx=ctx)
        return latest


def run_context(run: Run) -> TenantContext:
    """Return the tenant context a shadow run must act under."""
    return context_for_run(run.tenant_id, run.team_id, run.attribution_key)


def _latency_ms(run: Run) -> int:
    if run.started_at is None or run.finished_at is None:
        return 0
    return max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))
