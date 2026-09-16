"""Tests for the execution gate protocols and phase-1 defaults."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import ActionClass, DecisionOutcome, PolicyContext
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import BudgetLevel, UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.gates import (
    ManifestSandboxGate,
    NullRunExecutor,
    PermissivePolicyGate,
    RegistryCertificationGate,
    UnlimitedBudgetGate,
)
from hiveplane.execution.models import RunContext
from hiveplane.registry.models import AdmissionDecision


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class _FakeRegistry:
    def __init__(self, decision: AdmissionDecision, model: str | None) -> None:
        self._decision = decision
        self._model = model
        self.calls: list[tuple[str, AdmissionContext]] = []

    def check_admission(self, workload: str, context: AdmissionContext) -> AdmissionDecision:
        self.calls.append((workload, context))
        return self._decision

    def list_attestations(self, workload: str) -> list[object]:
        if self._model is None:
            return []

        class _Attestation:
            model_identity = self._model

        return [_Attestation()]


def test_registry_certification_gate_delegates() -> None:
    decision = AdmissionDecision(
        workload="agent-1",
        context=AdmissionContext.PRODUCTION,
        admitted=True,
        actual_status="certified",  # type: ignore[arg-type]
    )
    registry = _FakeRegistry(decision, "openai/gpt-4o/2024-08-06")
    gate = RegistryCertificationGate(registry)  # type: ignore[arg-type]
    assert gate.check_admission("agent-1", AdmissionContext.PRODUCTION).admitted is True
    assert registry.calls == [("agent-1", AdmissionContext.PRODUCTION)]
    assert gate.attestation_model("agent-1") == "openai/gpt-4o/2024-08-06"


def test_registry_certification_gate_no_attestation() -> None:
    decision = AdmissionDecision(
        workload="agent-1",
        context=AdmissionContext.SANDBOX,
        admitted=True,
        actual_status="uncertified",  # type: ignore[arg-type]
    )
    gate = RegistryCertificationGate(_FakeRegistry(decision, None))  # type: ignore[arg-type]
    assert gate.attestation_model("agent-1") is None


def test_permissive_policy_gate_allows() -> None:
    decision = PermissivePolicyGate(_clock).evaluate(
        PolicyContext(run_id="run-1", workload="agent-1", environment=AdmissionContext.SANDBOX)
    )
    assert decision.outcome is DecisionOutcome.ALLOW


def test_unlimited_budget_gate_allows(make_manifest: Callable[..., AgentWorkload]) -> None:
    workload = make_manifest()
    gate = UnlimitedBudgetGate()
    check = gate.check(workload, AdmissionContext.PRODUCTION)
    assert check.allowed is True
    assert check.level is BudgetLevel.RUN
    usage = UsageReport(
        run_id="run-1",
        input_tokens=0,
        output_tokens=0,
        tool_calls=0,
        cost_usd=0.05,
        timestamp=_clock(),
    )
    assert gate.record_usage(workload, usage).check.allowed is True


def test_manifest_sandbox_gate(make_manifest: Callable[..., AgentWorkload]) -> None:
    gate = ManifestSandboxGate()
    with_sandbox = make_manifest(
        sandbox={
            "enabled": True,
            "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
        }
    )
    assert gate.required(with_sandbox, AdmissionContext.PRODUCTION, None) is True
    assert gate.required(with_sandbox, AdmissionContext.SANDBOX, None) is True
    without = make_manifest()
    assert gate.required(without, AdmissionContext.PRODUCTION, None) is False
    assert gate.required(without, AdmissionContext.PRODUCTION, ActionClass.DESTRUCTIVE) is True


def test_null_run_executor_tracks_state(make_manifest: Callable[..., AgentWorkload]) -> None:
    executor = NullRunExecutor()
    run = Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.QUEUED,
        created_at=_clock(),
        updated_at=_clock(),
    )
    executor.start(RunContext(run=run, workload=make_manifest(), sandbox=False))
    assert executor.status("run-1") is RunState.RUNNING
    assert executor.pause("run-1") is True
    assert executor.status("run-1") is RunState.PAUSED
    assert executor.resume("run-1") is True
    assert executor.status("run-1") is RunState.RUNNING
    executor.cancel("run-1")
    assert executor.status("run-1") is RunState.CANCELLED
    assert executor.usage("run-1") is None
    assert executor.status("unknown") is RunState.QUEUED
