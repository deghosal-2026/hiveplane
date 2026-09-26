"""Tests for shadow runs (M37-01..M37-07)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine

from hiveplane.config import Settings
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.progressive.errors import (
    ShadowBudgetExceededError,
    ShadowNotFoundError,
)
from hiveplane.progressive.models import (
    OutcomeDiff,
    ShadowOutcome,
    ShadowReport,
    ShadowRun,
    ShadowStatus,
)
from hiveplane.progressive.shadow import ShadowService, diff_shadow
from hiveplane.progressive.store import (
    InMemoryProgressiveStore,
    PostgresProgressiveStore,
    build_progressive_store,
)
from postgres import ensure_schema

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _run(
    run_id: str = "run-1", *, task: dict[str, Any] | None = None, cost: float = 0.10
) -> Run:
    return Run(
        id=run_id,
        workload_id="repo-agent",
        caller="operator",
        state=RunState.COMPLETED,
        context=AdmissionContext.PRODUCTION,
        task=task if task is not None else {"ticket": "T-1"},
        result={"ok": True},
        cost_usd=cost,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
        finished_at=_NOW,
    )


class _Runner:
    """A scripted shadow runner that records the requests it receives."""

    def __init__(self, outcome: ShadowOutcome | None = None) -> None:
        self.requests: list[tuple[Run, str, int | None, str]] = []
        self._outcome = outcome or ShadowOutcome(
            status=ShadowStatus.COMPLETED,
            result={"ok": True, "variant": "candidate"},
            cost_usd=0.05,
            latency_ms=120,
            tool_calls=[{"tool_id": "search", "outcome": "allowed"}],
            policy_decisions=[{"rule": "allow", "decision": "allowed"}],
        )

    def run(
        self,
        production_run: Run,
        *,
        candidate_workload_id: str,
        candidate_version: int | None,
        budget_id: str,
    ) -> ShadowOutcome:
        self.requests.append(
            (production_run, candidate_workload_id, candidate_version, budget_id)
        )
        return self._outcome


def _service(
    runner: _Runner | None = None, *, budget_cap_usd: float | None = None
) -> tuple[ShadowService, _Runner]:
    runner = runner or _Runner()
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"shadow-{counter['n']}"

    return (
        ShadowService(
            InMemoryProgressiveStore(),
            runner=runner,
            budget_cap_usd=budget_cap_usd,
            clock=lambda: _NOW,
            id_factory=_id,
        ),
        runner,
    )


def test_shadow_run_mirrors_its_production_run() -> None:
    service, runner = _service()

    shadow = service.start(
        _run(), candidate_workload_id="repo-agent", candidate_version=2
    )

    assert shadow.shadow_run_id == "shadow-1"
    assert shadow.production_run_id == "run-1"
    assert shadow.candidate_workload_id == "repo-agent"
    assert shadow.candidate_version == 2
    assert shadow.input_ref == "run:run-1"
    assert shadow.budget_id == "shadow"
    assert shadow.tenant_id == "default"
    assert shadow.status is ShadowStatus.COMPLETED
    # the runner saw the paired production run and its shared task
    production_run, candidate, version, budget = runner.requests[0]
    assert production_run.id == "run-1"
    assert production_run.task == {"ticket": "T-1"}
    assert (candidate, version, budget) == ("repo-agent", 2, "shadow")
    assert service.get("shadow-1").shadow_run_id == "shadow-1"


def test_shadow_run_records_outcome_evidence() -> None:
    service, _ = _service()

    shadow = service.start(_run(), candidate_workload_id="repo-agent")

    assert shadow.result == {"ok": True, "variant": "candidate"}
    assert shadow.cost_usd == pytest.approx(0.05)
    assert shadow.latency_ms == 120
    assert shadow.tool_calls == [{"tool_id": "search", "outcome": "allowed"}]
    assert shadow.policy_decisions == [{"rule": "allow", "decision": "allowed"}]


def test_shadow_budget_is_separate_and_capped() -> None:
    service, _ = _service(budget_cap_usd=0.05)
    service._store.add_shadow_run(  # seed a prior shadow spend on the same budget
        ShadowRun(
            shadow_run_id="shadow-0",
            candidate_workload_id="repo-agent",
            production_run_id="run-0",
            input_ref="run:run-0",
            cost_usd=0.05,
            budget_id="shadow",
            status=ShadowStatus.COMPLETED,
            created_at=_NOW,
            tenant_id="default",
        )
    )

    with pytest.raises(ShadowBudgetExceededError):
        service.start(_run(), candidate_workload_id="repo-agent")


def test_list_shadow_runs_by_budget_and_workload() -> None:
    service, _ = _service()
    service.start(_run("run-1"), candidate_workload_id="repo-agent")
    service.start(_run("run-2"), candidate_workload_id="docs-agent")

    assert [s.production_run_id for s in service.list(budget_id="shadow")] == [
        "run-1",
        "run-2",
    ]
    assert [s.production_run_id for s in service.list(workload="docs-agent")] == ["run-2"]


def test_get_unknown_shadow_run_raises() -> None:
    service, _ = _service()

    with pytest.raises(ShadowNotFoundError):
        service.get("ghost")


def test_report_diffs_outputs_tools_cost_latency_and_policy() -> None:
    service, _ = _service()
    service.start(_run(), candidate_workload_id="repo-agent")
    production = _run()

    report = service.report("shadow-1", production)

    assert isinstance(report, ShadowReport)
    diff = report.outcome_diff
    assert isinstance(diff, OutcomeDiff)
    assert diff.output_changed is True
    assert diff.production_output == {"ok": True}
    assert diff.candidate_output == {"ok": True, "variant": "candidate"}
    assert diff.cost_delta_usd == pytest.approx(-0.05)
    assert diff.latency_delta_ms == 120
    assert diff.tool_calls_added == [{"tool_id": "search", "outcome": "allowed"}]
    assert diff.policy_decisions_added == [{"rule": "allow", "decision": "allowed"}]


def test_diff_shadow_marks_identical_outputs_unchanged() -> None:
    production = _run()
    shadow = ShadowRun(
        shadow_run_id="shadow-1",
        candidate_workload_id="repo-agent",
        production_run_id="run-1",
        input_ref="run:run-1",
        result={"ok": True},
        cost_usd=0.10,
        latency_ms=0,
        budget_id="shadow",
        status=ShadowStatus.COMPLETED,
        created_at=_NOW,
        tenant_id="default",
    )

    diff = diff_shadow(production, shadow, production_latency_ms=0)

    assert diff.output_changed is False
    assert diff.cost_delta_usd == pytest.approx(0.0)


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_progressive_store(Settings()), InMemoryProgressiveStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_progressive_store(settings), PostgresProgressiveStore)


def test_postgres_shadow_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresProgressiveStore(pg_engine)
    store.clear()
    service = ShadowService(
        store,
        runner=_Runner(),
        clock=lambda: _NOW,
        id_factory=lambda: "shadow-1",
    )
    service.start(_run(), candidate_workload_id="repo-agent", candidate_version=2)

    reopened = PostgresProgressiveStore(pg_engine)

    shadow = reopened.get_shadow_run("shadow-1")
    assert shadow is not None
    assert shadow.candidate_version == 2
    assert reopened.list_shadow_runs(budget_id="shadow")[0].shadow_run_id == "shadow-1"


def test_shadow_tenant_isolation() -> None:
    from hiveplane.tenancy import TenantContext

    service, _ = _service()
    service.start(_run(), candidate_workload_id="repo-agent")

    assert service.list(ctx=TenantContext(tenant_id="other")) == []
