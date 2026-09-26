"""Tests for the admission pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import DecisionOutcome, PolicyContext, PolicyDecision
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import BudgetCheck, BudgetLevel, BudgetOutcome, UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import AdmissionOutcome
from hiveplane.registry.models import AdmissionDecision


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _run(context: AdmissionContext, model: str | None = "m1") -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.QUEUED,
        model_identity=model,
        created_at=_clock(),
        updated_at=_clock(),
        context=context,
    )


class _Cert:
    def __init__(self, admitted: bool, model: str | None = None) -> None:
        self._admitted = admitted
        self._model = model

    def check_admission(self, workload: str, context: AdmissionContext) -> AdmissionDecision:
        return AdmissionDecision(
            workload=workload,
            context=context,
            admitted=self._admitted,
            actual_status="certified" if self._admitted else "uncertified",  # type: ignore[arg-type]
            reason=None if self._admitted else "not certified",
        )

    def attestation_model(self, workload: str) -> str | None:
        return self._model


class _Policy:
    def __init__(self, outcome: DecisionOutcome) -> None:
        self._outcome = outcome

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        return PolicyDecision(
            run_id=context.run_id,
            outcome=self._outcome,
            reason="test",
            rule="test",
            timestamp=_clock(),
        )


class _Budget:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck:
        return BudgetCheck(
            allowed=self._allowed,
            level=BudgetLevel.RUN,
            limit_usd=1.0,
            spent_usd=0.0 if self._allowed else 1.0,
            remaining_usd=1.0 if self._allowed else 0.0,
            reason=None if self._allowed else "budget exhausted",
        )

    def record_usage(self, workload: AgentWorkload, report: UsageReport) -> BudgetOutcome:
        raise AssertionError("not used during admission")


class _Sandbox:
    def __init__(self, required: bool) -> None:
        self._required = required

    def required(
        self, workload: AgentWorkload, context: AdmissionContext, action_class: object
    ) -> bool:
        return self._required


def _pipeline(
    *,
    cert: _Cert,
    policy: _Policy | None = None,
    budget: _Budget | None = None,
    sandbox: _Sandbox | None = None,
) -> AdmissionPipeline:
    return AdmissionPipeline(
        cert,
        policy or _Policy(DecisionOutcome.ALLOW),
        budget or _Budget(True),
        sandbox or _Sandbox(False),
        clock=_clock,
    )


def test_admitted_when_all_checks_pass(make_manifest: Callable[..., AgentWorkload]) -> None:
    workload = make_manifest()
    result = _pipeline(cert=_Cert(True, "m1")).check(_run(AdmissionContext.PRODUCTION), workload)
    assert result.outcome is AdmissionOutcome.ADMITTED
    assert [c.step for c in result.checks] == [
        "certification",
        "model_identity",
        "budget",
        "policy",
        "sandbox",
    ]


def test_refused_when_certification_fails(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(False)).check(_run(AdmissionContext.PRODUCTION), make_manifest())
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.refused_reason == "not certified"
    assert [c.step for c in result.checks] == ["certification"]


def test_refused_on_model_swap(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "other-model")).check(
        _run(AdmissionContext.PRODUCTION, model="m1"), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.refused_reason is not None and "model" in result.refused_reason


def test_refused_when_budget_fails(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "m1"), budget=_Budget(False)).check(
        _run(AdmissionContext.PRODUCTION), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.refused_reason == "budget exhausted"


def test_refused_when_policy_denies(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "m1"), policy=_Policy(DecisionOutcome.DENY)).check(
        _run(AdmissionContext.PRODUCTION), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.refused_reason == "test"


def test_escalation_pauses_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "m1"), policy=_Policy(DecisionOutcome.ESCALATE)).check(
        _run(AdmissionContext.PRODUCTION), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.ADMITTED
    assert result.escalation_required is True


def test_sandbox_context_marks_sandbox_only(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    result = _pipeline(cert=_Cert(True, "m1"), sandbox=_Sandbox(True)).check(
        _run(AdmissionContext.SANDBOX), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.SANDBOX_ONLY
    assert result.sandbox is True


def test_model_identity_is_skipped_when_run_has_none(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    result = _pipeline(cert=_Cert(True, "other-model")).check(
        _run(AdmissionContext.SANDBOX, model=None), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.SANDBOX_ONLY


def test_require_approval_forces_escalation(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    result = _pipeline(cert=_Cert(True, "m1")).check(
        _run(AdmissionContext.PRODUCTION), make_manifest(), require_approval=True
    )
    assert result.outcome is AdmissionOutcome.ADMITTED
    assert result.escalation_required is True


def test_require_approval_defaults_off(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    result = _pipeline(cert=_Cert(True, "m1")).check(
        _run(AdmissionContext.PRODUCTION), make_manifest()
    )
    assert result.escalation_required is False
