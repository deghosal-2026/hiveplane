"""The tool-call boundary: policy, egress, and shaping for adapter tool calls.

Adapters (Part 8) route every tool call here. This is the live path where
deny-by-default policy, restricted egress, and tool-output shaping are enforced
(mid-run, as opposed to admission), and where policy decisions are recorded as
run events (T2, T5, T13, T14; DD-13).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hiveplane import metrics, telemetry
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.decision import (
    ActionClass,
    DataSensitivity,
    DecisionOutcome,
    PolicyContext,
    PolicyDecision,
)
from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.sandbox import EgressMode, EgressSpec
from hiveplane.core.tools import ToolsSpec, ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.gates import ApprovalRequests, PolicyGate
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.tool_executor import ToolExecutor
from hiveplane.registry.service import RegistryService
from hiveplane.sandbox.egress import EgressGuard
from hiveplane.sandbox.errors import EgressDeniedError
from hiveplane.shaping.injection import InjectionVerdict
from hiveplane.shaping.pipeline import ShapedOutput, ShapingPipeline


class RunAccess(Protocol):
    """The run operations the tool boundary needs."""

    def get(self, run_id: str) -> Run: ...

    def intervene(self, run_id: str, action: InterventionAction, *, actor: str) -> Run: ...

    def record_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        detail: str | None = None,
    ) -> None: ...


class ToolCallOutcome(StrEnum):
    """The disposition of a tool call at the control-plane boundary."""

    ALLOWED = "allowed"
    DENIED = "denied"
    ESCALATED = "escalated"
    BLOCKED_INJECTION = "blocked_injection"


class ToolCallRequest(BaseModel):
    """A tool call an adapter wants the control plane to authorize."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    action_class: ActionClass | None = None
    data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    tool_trust: ToolTrustLevel | None = None
    output: str | None = None
    host: str | None = None


class ToolCallResult(BaseModel):
    """The boundary's decision, with any shaped output and approval id."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    outcome: ToolCallOutcome
    rule: str | None = None
    reason: str | None = None
    shaped_output: ShapedOutput | None = None
    approval_id: str | None = None
    egress_checked: bool = False


class ToolGateway:
    """Authorizes tool calls against policy, egress, and shaping."""

    def __init__(
        self,
        registry: RegistryService,
        policy: PolicyGate,
        runs: RunAccess,
        *,
        shaping: ShapingPipeline | None = None,
        approvals: ApprovalRequests | None = None,
        executor: ToolExecutor | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._runs = runs
        self._shaping = shaping
        self._approvals = approvals
        self._executor = executor
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(self, run_id: str, request: ToolCallRequest) -> ToolCallResult:
        """Evaluate and shape a tool call for a run, as a ``tool_call`` span."""
        run = self._runs.get(run_id)
        workload = self._registry.get(run.workload_id).manifest
        with telemetry.span(
            "tool_call",
            run=run,
            workload=workload,
            attributes={"tool_id": request.tool_id},
        ) as active:
            result = self._invoke(run_id, run, workload, request)
            active.set_attribute("outcome", result.outcome.value)
            if result.rule is not None:
                active.set_attribute("rule", result.rule)
            trust = request.tool_trust or _trust_for(workload.spec.tools, request.tool_id)
            metrics.get_metrics().record_tool_call(
                workload=workload.name,
                team=workload.team,
                tool_id=request.tool_id,
                trust_level=trust.value,
                outcome=result.outcome.value,
            )
            return result

    def _invoke(
        self, run_id: str, run: Run, workload: AgentWorkload, request: ToolCallRequest
    ) -> ToolCallResult:
        """Evaluate and shape a tool call, recording the decision."""
        spec = workload.spec
        tool_trust = request.tool_trust or _trust_for(spec.tools, request.tool_id)
        context = PolicyContext(
            run_id=run_id,
            workload=workload.name,
            team=workload.team,
            environment=run.context or AdmissionContext.SANDBOX,
            action_class=request.action_class,
            tool_id=request.tool_id,
            tool_trust=tool_trust,
            data_sensitivity=request.data_sensitivity,
            certification_status=workload.certification_status,
            tools=spec.tools,
            approval_required_for=spec.approvals.required_for,
        )
        decision = self._policy.evaluate(context)
        self._runs.record_event(
            run_id,
            EventType.POLICY_DECISION,
            "policy",
            detail=f"{decision.rule}: {decision.reason}",
        )
        if decision.outcome is DecisionOutcome.DENY:
            return self._result(run_id, request, ToolCallOutcome.DENIED, decision)
        if decision.outcome is DecisionOutcome.BLOCK_INJECTION:
            return self._result(run_id, request, ToolCallOutcome.BLOCKED_INJECTION, decision)
        if decision.outcome is DecisionOutcome.ESCALATE:
            granted = self._granted_approval(run_id, decision.rule)
            if granted is not None:
                # Re-dispatch (M23, #129): the operator already approved this
                # call, so execute it and attach the approval record instead of
                # escalating again.
                approval_id: str | None = granted.approval_id
            else:
                approval_id = self._escalate(run, workload.name, decision, request)
                return self._result(
                    run_id,
                    request,
                    ToolCallOutcome.ESCALATED,
                    decision,
                    approval_id=approval_id,
                )
        else:
            approval_id = None

        egress = self._check_egress(run_id, workload, request)
        if egress is not None:
            return egress
        output = self._resolve_output(request)
        shaped = self._shape(workload, output)
        if (
            shaped is not None
            and shaped.injection is not None
            and shaped.injection.verdict is InjectionVerdict.BLOCK
        ):
            return ToolCallResult(
                run_id=run_id,
                tool_id=request.tool_id,
                outcome=ToolCallOutcome.BLOCKED_INJECTION,
                rule="injection.scan",
                reason="tool output contains injection patterns",
                shaped_output=shaped,
            )
        self._runs.record_event(run_id, EventType.TOOL_CALL, "adapter", detail=request.tool_id)
        return self._result(
            run_id,
            request,
            ToolCallOutcome.ALLOWED,
            decision,
            shaped_output=shaped,
            egress_checked=request.host is not None,
            approval_id=approval_id,
        )

    def _granted_approval(self, run_id: str, rule: str) -> ApprovalRecord | None:
        """Return an approved approval for this run and rule, if any (#129)."""
        if self._approvals is None:
            return None
        for record in self._approvals.list(status=ApprovalStatus.APPROVED, run_id=run_id):
            if record.rule == rule:
                return record
        return None

    def _escalate(
        self,
        run: Run,
        workload: str,
        decision: PolicyDecision,
        request: ToolCallRequest,
    ) -> str | None:
        if run.state is RunState.RUNNING:
            self._runs.intervene(run.id, InterventionAction.PAUSE, actor="policy")
        if self._approvals is None:
            return None
        record = self._approvals.request(
            run_id=run.id,
            workload=workload,
            rule=decision.rule,
            reason=decision.reason,
            action_class=request.action_class,
        )
        return record.approval_id

    def _check_egress(
        self, run_id: str, workload: AgentWorkload, request: ToolCallRequest
    ) -> ToolCallResult | None:
        if request.host is None:
            return None
        sandbox = workload.spec.sandbox
        egress_spec = (
            sandbox.egress if sandbox is not None else EgressSpec(mode=EgressMode.RESTRICTED)
        )
        try:
            EgressGuard(egress_spec).check(request.host)
        except EgressDeniedError as exc:
            return ToolCallResult(
                run_id=run_id,
                tool_id=request.tool_id,
                outcome=ToolCallOutcome.DENIED,
                rule="egress.denied",
                reason=str(exc),
            )
        return None

    def _resolve_output(self, request: ToolCallRequest) -> str | None:
        """Return caller-provided output, else execute the tool via the executor."""
        if request.output is not None:
            return request.output
        if self._executor is None:
            return None
        return self._executor.execute(request.tool_id)

    def _shape(self, workload: AgentWorkload, output: str | None) -> ShapedOutput | None:
        if output is None or self._shaping is None:
            return None
        spec = workload.spec.output_shaping
        if spec is None:
            return None
        return self._shaping.apply(output, spec)

    @staticmethod
    def _result(
        run_id: str,
        request: ToolCallRequest,
        outcome: ToolCallOutcome,
        decision: PolicyDecision,
        *,
        shaped_output: ShapedOutput | None = None,
        approval_id: str | None = None,
        egress_checked: bool = False,
    ) -> ToolCallResult:
        return ToolCallResult(
            run_id=run_id,
            tool_id=request.tool_id,
            outcome=outcome,
            rule=decision.rule,
            reason=decision.reason,
            shaped_output=shaped_output,
            approval_id=approval_id,
            egress_checked=egress_checked,
        )


def _trust_for(tools: ToolsSpec, tool_id: str) -> ToolTrustLevel:
    for entry in tools.allow:
        if entry.tool_id == tool_id:
            return entry.trust_level
    return tools.default_trust
