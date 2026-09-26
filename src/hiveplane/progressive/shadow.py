"""Shadow runs: mirror a production task on a candidate without delivery (M37).

A shadow run executes a candidate workload on the **same task** as a paired
production run, never delivers its result to any fan-out channel, defaults to a
read-only tool policy, and bills a separate shadow budget.
"""

from __future__ import annotations

import builtins
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue

from hiveplane.core.run import Run
from hiveplane.progressive.errors import ShadowBudgetExceededError, ShadowNotFoundError
from hiveplane.progressive.models import (
    OutcomeDiff,
    ShadowOutcome,
    ShadowReport,
    ShadowRun,
)
from hiveplane.progressive.store import ProgressiveStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class ShadowRunner(Protocol):
    """Executes a candidate on a mirrored task and returns shadow evidence."""

    def run(
        self,
        production_run: Run,
        *,
        candidate_workload_id: str,
        candidate_version: int | None,
        budget_id: str,
    ) -> ShadowOutcome: ...


def _latency_ms(run: Run) -> int:
    if run.started_at is None or run.finished_at is None:
        return 0
    return max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))


def diff_shadow(
    production: Run,
    shadow: ShadowRun,
    *,
    production_latency_ms: int | None = None,
    production_tool_calls: list[dict[str, JsonValue]] | None = None,
    production_policy_decisions: list[dict[str, JsonValue]] | None = None,
) -> OutcomeDiff:
    """Compare a production run with its shadow, surfacing meaningful deltas."""
    latency = _latency_ms(production) if production_latency_ms is None else production_latency_ms
    prod_tools = production_tool_calls or []
    prod_policy = production_policy_decisions or []
    return OutcomeDiff(
        output_changed=production.result != shadow.result,
        production_output=production.result,
        candidate_output=shadow.result,
        cost_delta_usd=shadow.cost_usd - production.cost_usd,
        latency_delta_ms=shadow.latency_ms - latency,
        tool_calls_added=[call for call in shadow.tool_calls if call not in prod_tools],
        tool_calls_removed=[call for call in prod_tools if call not in shadow.tool_calls],
        policy_decisions_added=[
            decision for decision in shadow.policy_decisions if decision not in prod_policy
        ],
        policy_decisions_removed=[
            decision for decision in prod_policy if decision not in shadow.policy_decisions
        ],
    )


class ShadowService:
    """Starts, records, and reports shadow runs."""

    def __init__(
        self,
        store: ProgressiveStore,
        *,
        runner: ShadowRunner,
        budget_cap_usd: float | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._budget_cap_usd = budget_cap_usd
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"shadow-{uuid.uuid4().hex[:12]}")

    def start(
        self,
        production_run: Run,
        *,
        candidate_workload_id: str,
        candidate_version: int | None = None,
        budget_id: str = "shadow",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ShadowRun:
        """Mirror a production run on the candidate and record the outcome."""
        spent = sum(
            record.cost_usd
            for record in self._store.list_shadow_runs(budget_id=budget_id, ctx=ctx)
        )
        if self._budget_cap_usd is not None and spent >= self._budget_cap_usd:
            raise ShadowBudgetExceededError(budget_id)
        outcome = self._runner.run(
            production_run,
            candidate_workload_id=candidate_workload_id,
            candidate_version=candidate_version,
            budget_id=budget_id,
        )
        record = ShadowRun(
            shadow_run_id=self._id_factory(),
            candidate_workload_id=candidate_workload_id,
            production_run_id=production_run.id,
            input_ref=f"run:{production_run.id}",
            candidate_version=candidate_version,
            result=outcome.result,
            cost_usd=outcome.cost_usd,
            latency_ms=outcome.latency_ms,
            tool_calls=outcome.tool_calls,
            policy_decisions=outcome.policy_decisions,
            budget_id=budget_id,
            status=outcome.status,
            failure_reason=outcome.failure_reason,
            created_at=self._clock(),
            tenant_id=production_run.tenant_id,
        )
        self._store.add_shadow_run(record, ctx=ctx)
        return record

    def get(
        self, shadow_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ShadowRun:
        """Return a shadow run, or raise when absent/out of scope."""
        record = self._store.get_shadow_run(shadow_run_id, ctx=ctx)
        if record is None:
            raise ShadowNotFoundError(shadow_run_id)
        return record

    def list(
        self,
        *,
        workload: str | None = None,
        budget_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ShadowRun]:
        """List shadow runs, optionally filtered."""
        return self._store.list_shadow_runs(
            workload=workload, budget_id=budget_id, ctx=ctx
        )

    def report(
        self,
        shadow_run_id: str,
        production_run: Run,
        *,
        production_latency_ms: int | None = None,
        production_tool_calls: builtins.list[dict[str, JsonValue]] | None = None,
        production_policy_decisions: builtins.list[dict[str, JsonValue]] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ShadowReport:
        """Return the outcome diff between a shadow run and its production pair."""
        shadow = self.get(shadow_run_id, ctx=ctx)
        return ShadowReport(
            shadow_run_id=shadow.shadow_run_id,
            production_run_id=shadow.production_run_id,
            candidate_workload_id=shadow.candidate_workload_id,
            outcome_diff=diff_shadow(
                production_run,
                shadow,
                production_latency_ms=production_latency_ms,
                production_tool_calls=production_tool_calls,
                production_policy_decisions=production_policy_decisions,
            ),
        )
