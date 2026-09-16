"""Run-lifecycle gate and executor protocols plus phase-1 defaults.

The protocols are the seams the lifecycle depends on. The phase-1 defaults keep
the control plane runnable before the policy, budget, sandbox, and adapter
engines exist; each is replaced by the real implementation in its own phase.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.decision import (
    ActionClass,
    DecisionOutcome,
    PolicyContext,
    PolicyDecision,
)
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import BudgetCheck, BudgetLevel, UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import DeliveryRecord, RunContext
from hiveplane.registry.models import AdmissionDecision
from hiveplane.registry.service import RegistryService


class CertificationGate(Protocol):
    """Reads certification status and attestation model for admission."""

    def check_admission(self, workload: str, context: AdmissionContext) -> AdmissionDecision: ...

    def attestation_model(self, workload: str) -> str | None: ...


class RegistryCertificationGate:
    """CertificationGate backed by the registry service."""

    def __init__(self, registry: RegistryService) -> None:
        self._registry = registry

    def check_admission(self, workload: str, context: AdmissionContext) -> AdmissionDecision:
        """Return the registry admission decision for a workload and context."""
        return self._registry.check_admission(workload, context)

    def attestation_model(self, workload: str) -> str | None:
        """Return the model identity bound to the latest attestation, if any."""
        attestations = self._registry.list_attestations(workload)
        return attestations[-1].model_identity if attestations else None


class PolicyGate(Protocol):
    """Evaluates context-aware policy for an admission or tool call."""

    def evaluate(self, context: PolicyContext) -> PolicyDecision: ...


class PermissivePolicyGate:
    """Phase-1 default that allows everything; replaced by the policy engine."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        """Return an allow decision."""
        return PolicyDecision(
            run_id=context.run_id,
            outcome=DecisionOutcome.ALLOW,
            reason="policy engine not configured",
            rule="default-allow",
            action_class=context.action_class,
            timestamp=self._clock(),
            certification_status=context.certification_status,
        )


class BudgetGate(Protocol):
    """Checks budget headroom and records usage."""

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck: ...

    def record_usage(self, report: UsageReport) -> BudgetCheck: ...


class UnlimitedBudgetGate:
    """Phase-1 default that never blocks; replaced by the budget service."""

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck:
        """Return an allow check against the workload's per-run budget."""
        limit = workload.spec.budget.per_run_usd
        return BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=limit,
            spent_usd=0.0,
            remaining_usd=limit,
        )

    def record_usage(self, report: UsageReport) -> BudgetCheck:
        """Return an allow check for a usage report."""
        return BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=report.cost_usd,
            spent_usd=report.cost_usd,
            remaining_usd=0.0,
        )


class SandboxGate(Protocol):
    """Decides whether a run must execute in the sandbox."""

    def required(
        self,
        workload: AgentWorkload,
        context: AdmissionContext,
        action_class: ActionClass | None,
    ) -> bool: ...


class ManifestSandboxGate:
    """Sandbox requirement derived from the manifest and run context."""

    def required(
        self,
        workload: AgentWorkload,
        context: AdmissionContext,
        action_class: ActionClass | None,
    ) -> bool:
        """Return True when the workload, context, or action class needs isolation."""
        if context is AdmissionContext.SANDBOX:
            return True
        if action_class is ActionClass.DESTRUCTIVE:
            return True
        sandbox = workload.spec.sandbox
        return sandbox is not None and sandbox.enabled


class RunExecutor(Protocol):
    """The execution seam implemented by runtime adapters."""

    def start(self, context: RunContext) -> None: ...

    def pause(self, run_id: str) -> bool: ...

    def resume(self, run_id: str) -> bool: ...

    def cancel(self, run_id: str) -> None: ...

    def status(self, run_id: str) -> RunState: ...

    def usage(self, run_id: str) -> UsageReport | None: ...


class FanOut(Protocol):
    """Delivers terminal run outcomes to configured destinations."""

    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]: ...

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]: ...


class ApprovalRequests(Protocol):
    """Requests a human approval for an escalated run."""

    def request(
        self, *, run_id: str, workload: str, rule: str, reason: str
    ) -> ApprovalRecord: ...


class NullRunExecutor:
    """Phase-1 default executor that records nothing; replaced by adapters."""

    def __init__(self) -> None:
        self._states: dict[str, RunState] = {}

    def start(self, context: RunContext) -> None:
        """Record the run as running."""
        self._states[context.run.id] = RunState.RUNNING

    def pause(self, run_id: str) -> bool:
        """Accept a pause request."""
        self._states[run_id] = RunState.PAUSED
        return True

    def resume(self, run_id: str) -> bool:
        """Accept a resume request."""
        self._states[run_id] = RunState.RUNNING
        return True

    def cancel(self, run_id: str) -> None:
        """Record the run as cancelled."""
        self._states[run_id] = RunState.CANCELLED

    def status(self, run_id: str) -> RunState:
        """Return the last recorded state."""
        return self._states.get(run_id, RunState.QUEUED)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return no usage."""
        return None
