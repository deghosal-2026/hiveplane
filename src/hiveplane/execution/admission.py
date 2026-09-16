"""The admission pipeline: gate every run before it is queued."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import DecisionOutcome, PolicyContext
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.gates import BudgetGate, CertificationGate, PolicyGate, SandboxGate
from hiveplane.execution.models import AdmissionCheck, AdmissionOutcome, AdmissionResult


class AdmissionPipeline:
    """Evaluates certification, model binding, budget, policy, and sandbox."""

    def __init__(
        self,
        certification: CertificationGate,
        policy: PolicyGate,
        budget: BudgetGate,
        sandbox: SandboxGate,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._certification = certification
        self._policy = policy
        self._budget = budget
        self._sandbox = sandbox
        self._clock = clock or (lambda: datetime.now(UTC))

    def check(self, run: Run, workload: AgentWorkload) -> AdmissionResult:
        """Return the admission decision for a run against its workload."""
        context = run.context
        if context is None:
            raise ValueError("run.context is required for admission")
        checks: list[AdmissionCheck] = []

        decision = self._certification.check_admission(workload.name, context)
        checks.append(
            AdmissionCheck(
                step="certification",
                passed=decision.admitted,
                reason=decision.reason,
                rule="certification",
            )
        )
        if not decision.admitted:
            return self._refused(run, context, checks, decision.reason or "admission refused")

        bound_model = self._certification.attestation_model(workload.name)
        model_ok = (
            run.model_identity is None
            or bound_model is None
            or run.model_identity == bound_model
        )
        checks.append(
            AdmissionCheck(
                step="model_identity",
                passed=model_ok,
                reason=None if model_ok else "runtime model does not match attestation model",
                rule="model_binding",
            )
        )
        if not model_ok:
            return self._refused(
                run, context, checks, "model swap: runtime model differs from attestation"
            )

        budget = self._budget.check(workload, context)
        checks.append(
            AdmissionCheck(
                step="budget",
                passed=budget.allowed,
                reason=budget.reason,
                rule=budget.level.value,
            )
        )
        if not budget.allowed:
            return self._refused(run, context, checks, budget.reason or "budget exhausted")

        policy_context = PolicyContext(
            run_id=run.id,
            workload=workload.name,
            team=workload.team,
            environment=context,
            certification_status=workload.certification_status,
        )
        policy = self._policy.evaluate(policy_context)
        checks.append(
            AdmissionCheck(
                step="policy",
                passed=policy.outcome is DecisionOutcome.ALLOW,
                reason=policy.reason,
                rule=policy.rule,
            )
        )
        if policy.outcome in (DecisionOutcome.DENY, DecisionOutcome.BLOCK_INJECTION):
            return self._refused(run, context, checks, policy.reason)

        sandbox_required = self._sandbox.required(workload, context, policy.action_class)
        checks.append(AdmissionCheck(step="sandbox", passed=True, rule="sandbox"))
        outcome = (
            AdmissionOutcome.SANDBOX_ONLY
            if context is AdmissionContext.SANDBOX or sandbox_required
            else AdmissionOutcome.ADMITTED
        )
        return AdmissionResult(
            run_id=run.id,
            workload=workload.name,
            context=context,
            outcome=outcome,
            checks=checks,
            sandbox=sandbox_required,
            escalation_required=policy.outcome is DecisionOutcome.ESCALATE,
        )

    @staticmethod
    def _refused(
        run: Run,
        context: AdmissionContext,
        checks: list[AdmissionCheck],
        reason: str,
    ) -> AdmissionResult:
        return AdmissionResult(
            run_id=run.id,
            workload=run.workload_id,
            context=context,
            outcome=AdmissionOutcome.REFUSED,
            checks=checks,
            refused_reason=reason,
        )
