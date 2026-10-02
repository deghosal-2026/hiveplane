"""The admission pipeline: gate every run before it is queued."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane import metrics, telemetry
from hiveplane.core.decision import DecisionOutcome, PolicyContext
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.gates import (
    BudgetGate,
    CertificationGate,
    HaltGate,
    PolicyGate,
    SandboxGate,
)
from hiveplane.execution.models import AdmissionCheck, AdmissionOutcome, AdmissionResult
from hiveplane.tenancy.context import context_for_run


class AdmissionPipeline:
    """Evaluates certification, model binding, budget, policy, and sandbox."""

    def __init__(
        self,
        certification: CertificationGate,
        policy: PolicyGate,
        budget: BudgetGate,
        sandbox: SandboxGate,
        *,
        halt: HaltGate | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._certification = certification
        self._policy = policy
        self._budget = budget
        self._sandbox = sandbox
        self._halt = halt
        self._clock = clock or (lambda: datetime.now(UTC))

    def check(
        self, run: Run, workload: AgentWorkload, *, require_approval: bool = False
    ) -> AdmissionResult:
        """Return the admission decision for a run against its workload.

        ``require_approval`` forces escalation (a held, paused run) even when
        policy would otherwise allow — used by gated triggers. It never bypasses
        the certification, model, budget, or policy gates.
        """
        with telemetry.span("admission", run=run, workload=workload) as active:
            result = self._check(run, workload, require_approval=require_approval)
            active.set_attribute("outcome", result.outcome.value)
            return result

    def _check(
        self, run: Run, workload: AgentWorkload, *, require_approval: bool = False
    ) -> AdmissionResult:
        """Evaluate every admission gate for a run."""
        context = run.context
        if context is None:
            raise ValueError("run.context is required for admission")
        run_ctx = context_for_run(run.tenant_id, run.team_id, run.attribution_key)
        checks: list[AdmissionCheck] = []

        if self._halt is not None:
            try:
                halted = self._halt.halted(
                    tenant_id=run.tenant_id, workload=workload.name
                )
            except Exception:
                halted = True
            checks.append(
                AdmissionCheck(
                    step="halt",
                    passed=not halted,
                    reason="fleet halted: incident mode is active" if halted else None,
                    rule="incident",
                )
            )
            if halted:
                return self._refused(
                    run, context, checks, "fleet halted: incident mode is active"
                )

        decision = self._certification.check_admission(workload.name, context, ctx=run_ctx)
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

        bound_model = self._certification.attestation_model(workload.name, ctx=run_ctx)
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
            metrics.get_metrics().record_model_swap_block(workload=workload.name)
            return self._refused(
                run, context, checks, "model swap: runtime model differs from attestation"
            )

        budget = self._budget.check(workload, context, ctx=run_ctx)
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
            tenant_id=run.tenant_id,
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
            escalation_required=policy.outcome is DecisionOutcome.ESCALATE
            or require_approval,
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
