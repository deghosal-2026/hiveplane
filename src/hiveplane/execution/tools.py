"""The tool-call boundary: policy, egress, and shaping for adapter tool calls.

Adapters (Part 8) route every tool call here. This is the live path where
deny-by-default policy, restricted egress, and tool-output shaping are enforced
(mid-run, as opposed to admission), and where policy decisions are recorded as
run events (T2, T5, T13, T14; DD-13).
"""

from __future__ import annotations

import threading
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
from hiveplane.defense.guard import DefenseGuard
from hiveplane.defense.scanner import DetectorAction
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.gates import ApprovalRequests, PolicyGate
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.tool_executor import ToolExecutor
from hiveplane.guards.breaker import CircuitBreakerRegistry
from hiveplane.policy.kill_switch import KillSwitch
from hiveplane.registry.service import RegistryService
from hiveplane.sandbox.egress import EgressGuard
from hiveplane.sandbox.errors import EgressDeniedError
from hiveplane.shaping.injection import InjectionVerdict
from hiveplane.shaping.pipeline import ShapedOutput, ShapingPipeline
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import SYSTEM_CONTEXT, context_for_run

#: Run states in which the tool boundary must refuse calls (#502).
_TERMINAL_RUN_STATES = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)


class RunAccess(Protocol):
    """The run operations the tool boundary needs."""

    def get(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Run: ...

    def intervene(
        self,
        run_id: str,
        action: InterventionAction,
        *,
        actor: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Run: ...

    def record_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        detail: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
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
    port: int | None = None


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
        defense: DefenseGuard | None = None,
        kill_switch: KillSwitch | None = None,
        breaker: CircuitBreakerRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._runs = runs
        self._shaping = shaping
        self._approvals = approvals
        self._executor = executor
        self._defense = defense
        self._kill_switch = kill_switch
        self._breaker = breaker
        self._clock = clock or (lambda: datetime.now(UTC))
        self._consumed: dict[str, set[str]] = {}
        self._consumed_lock = threading.Lock()

    def reset_drive(self, run_id: str) -> None:
        """Clear consumption for a run so a re-driven attempt replays (#500).

        An approval authorizes one escalated call per execution attempt; a
        re-dispatch replays the run from the start and must re-use the same
        approvals rather than escalate the already-approved calls again.
        """
        with self._consumed_lock:
            self._consumed.pop(run_id, None)

    def _consume(self, run_id: str, approval_id: str) -> None:
        with self._consumed_lock:
            self._consumed.setdefault(run_id, set()).add(approval_id)

    def _consumed_ids(self, run_id: str) -> set[str]:
        with self._consumed_lock:
            return set(self._consumed.get(run_id, ()))

    def invoke(
        self,
        run_id: str,
        request: ToolCallRequest,
        *,
        ctx: TenantContext | None = None,
    ) -> ToolCallResult:
        """Evaluate and shape a tool call for a run, as a ``tool_call`` span.

        The run's own tenant is resolved internally and used for every read and
        write, so an adapter's in-process tool call is attributed correctly even
        though the adapter does not carry a tenant. When ``ctx`` is supplied (the
        public API), it is additionally enforced: a caller may only act on a run
        in its own tenant.
        """
        run = self._runs.get(run_id, ctx=SYSTEM_CONTEXT)
        if ctx is not None and not ctx.scopes(run.tenant_id):
            raise RunNotFoundError(run_id)
        run_ctx = context_for_run(run.tenant_id, run.team_id, run.attribution_key)
        workload = self._registry.get(run.workload_id, ctx=run_ctx).manifest
        with telemetry.span(
            "tool_call",
            run=run,
            workload=workload,
            attributes={"tool_id": request.tool_id},
        ) as active:
            result = self._invoke(run_id, run, workload, request, run_ctx)
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
        self,
        run_id: str,
        run: Run,
        workload: AgentWorkload,
        request: ToolCallRequest,
        ctx: TenantContext,
    ) -> ToolCallResult:
        """Evaluate and shape a tool call, recording the decision."""
        if run.state in _TERMINAL_RUN_STATES:
            # A run that has been cancelled/failed/completed must not keep
            # exercising tools; the boundary fails closed (#502).
            return ToolCallResult(
                run_id=run_id,
                tool_id=request.tool_id,
                outcome=ToolCallOutcome.DENIED,
                rule="run_not_running",
                reason=f"run is {run.state.value}; tool calls are refused",
            )
        spec = workload.spec
        tool_trust = request.tool_trust or _trust_for(spec.tools, request.tool_id)
        if not spec.tools.is_allowed(request.tool_id) and not spec.tools.is_denied(
            request.tool_id
        ):
            return ToolCallResult(
                run_id=run_id,
                tool_id=request.tool_id,
                outcome=ToolCallOutcome.DENIED,
                rule="tool_not_allowed",
                reason=f"tool {request.tool_id!r} is not in the workload allow-list",
            )
        if self._kill_switch is not None and self._kill_switch.is_disabled(request.tool_id):
            return ToolCallResult(
                run_id=run_id,
                tool_id=request.tool_id,
                outcome=ToolCallOutcome.DENIED,
                rule="kill_switch",
                reason=f"tool {request.tool_id!r} is disabled fleet-wide",
            )
        if self._breaker is not None and not self._breaker.allow("tool", request.tool_id):
            return ToolCallResult(
                run_id=run_id,
                tool_id=request.tool_id,
                outcome=ToolCallOutcome.DENIED,
                rule="circuit_open",
                reason=f"circuit breaker is open for tool {request.tool_id!r}",
            )
        if run.read_only and _is_destructive(request.action_class, tool_trust):
            return ToolCallResult(
                run_id=run_id,
                tool_id=request.tool_id,
                outcome=ToolCallOutcome.DENIED,
                rule="read_only.block",
                reason="shadow/read-only runs cannot call side-effecting tools",
            )
        if self._defense is not None and _is_destructive(request.action_class, tool_trust):
            taint = self._defense.gate_destructive(
                run_id=run_id,
                workload=workload.name,
                tool_id=request.tool_id,
                allow_untrusted=spec.tools.allows_untrusted(request.tool_id),
                tenant_id=run.tenant_id,
            )
            if taint.blocked:
                return ToolCallResult(
                    run_id=run_id,
                    tool_id=request.tool_id,
                    outcome=ToolCallOutcome.DENIED,
                    rule="taint.block",
                    reason=taint.reason,
                )
        context = PolicyContext(
            run_id=run_id,
            workload=workload.name,
            team=workload.team,
            tenant_id=run.tenant_id,
            environment=run.context or AdmissionContext.SANDBOX,
            action_class=request.action_class,
            tool_id=request.tool_id,
            tool_trust=tool_trust,
            data_sensitivity=request.data_sensitivity,
            certification_status=workload.certification_status,
            tools=spec.tools,
            approval_required_for=spec.approvals.required_for,
            time_windows=spec.time_windows,
            at=self._clock(),
        )
        decision = self._policy.evaluate(context)
        self._runs.record_event(
            run_id,
            EventType.POLICY_DECISION,
            "policy",
            detail=f"{decision.rule}: {decision.reason}",
            ctx=ctx,
        )
        if decision.outcome is DecisionOutcome.DENY:
            return self._result(run_id, request, ToolCallOutcome.DENIED, decision)
        if decision.outcome is DecisionOutcome.BLOCK_INJECTION:
            return self._result(run_id, request, ToolCallOutcome.BLOCKED_INJECTION, decision)
        if decision.outcome is DecisionOutcome.ESCALATE:
            granted = self._granted_approval(
                run_id, decision.rule, request.tool_id, ctx=ctx
            )
            if granted is not None:
                # Re-dispatch (M23, #129): the operator already approved this
                # call, so execute it and attach the approval record instead of
                # escalating again. Consumption (#500) binds it to one call per
                # execution attempt.
                self._consume(run_id, granted.approval_id)
                approval_id: str | None = granted.approval_id
            else:
                approval_id = self._escalate(run, workload.name, decision, request, ctx)
                return self._result(
                    run_id,
                    request,
                    ToolCallOutcome.ESCALATED,
                    decision,
                    approval_id=approval_id,
                )
        else:
            approval_id = None

        egress = self._check_egress(run_id, workload, request, tenant_id=run.tenant_id)
        if egress is not None:
            return egress
        try:
            output = self._resolve_output(request)
        except Exception:
            if self._breaker is not None:
                self._breaker.record("tool", request.tool_id, success=False)
            raise
        if self._breaker is not None:
            self._breaker.record("tool", request.tool_id, success=True)
        shaped = self._shape(workload, output)
        if self._defense is not None and output is not None:
            scan = self._defense.scan_output(
                run_id=run_id,
                workload=workload.name,
                tool_id=request.tool_id,
                text=output,
                tenant_id=run.tenant_id,
            )
            if scan.action is DetectorAction.BLOCK:
                return ToolCallResult(
                    run_id=run_id,
                    tool_id=request.tool_id,
                    outcome=ToolCallOutcome.BLOCKED_INJECTION,
                    rule="injection.scan",
                    reason="tool output contains injection patterns",
                    shaped_output=shaped,
                )
            self._defense.mark_untrusted(run_id=run_id, source_id=request.tool_id)
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
        self._runs.record_event(
            run_id, EventType.TOOL_CALL, "adapter", detail=request.tool_id, ctx=ctx
        )
        return self._result(
            run_id,
            request,
            ToolCallOutcome.ALLOWED,
            decision,
            shaped_output=shaped,
            egress_checked=request.host is not None,
            approval_id=approval_id,
        )

    def _granted_approval(
        self, run_id: str, rule: str, tool_id: str, *, ctx: TenantContext
    ) -> ApprovalRecord | None:
        """Return an unconsumed approved approval for this run, rule, and tool.

        Matching on ``tool_id`` (not just the rule) is what stops one approval
        for one destructive tool authorizing a different tool that escalates
        under the same rule (#500).
        """
        if self._approvals is None:
            return None
        used = self._consumed_ids(run_id)
        for record in self._approvals.list(
            status=ApprovalStatus.APPROVED, run_id=run_id, ctx=ctx
        ):
            if (
                record.rule == rule
                and record.tool_id == tool_id
                and record.approval_id not in used
            ):
                return record
        return None

    def _escalate(
        self,
        run: Run,
        workload: str,
        decision: PolicyDecision,
        request: ToolCallRequest,
        ctx: TenantContext,
    ) -> str | None:
        if run.state is RunState.RUNNING:
            self._runs.intervene(
                run.id, InterventionAction.PAUSE, actor="policy", ctx=ctx
            )
        if self._approvals is None:
            return None
        record = self._approvals.request(
            run_id=run.id,
            workload=workload,
            rule=decision.rule,
            reason=decision.reason,
            action_class=request.action_class,
            tool_id=request.tool_id,
            tenant_id=run.tenant_id,
            ctx=ctx,
        )
        return record.approval_id

    def _check_egress(
        self,
        run_id: str,
        workload: AgentWorkload,
        request: ToolCallRequest,
        *,
        tenant_id: str | None = None,
    ) -> ToolCallResult | None:
        if request.host is None:
            return None
        if self._defense is not None:
            decision = self._defense.check_egress(
                run_id=run_id,
                workload=workload.name,
                tool_id=request.tool_id,
                host=request.host,
                port=request.port,
                sandbox=workload.spec.sandbox,
                tenant_id=tenant_id,
            )
            if not decision.allowed:
                return ToolCallResult(
                    run_id=run_id,
                    tool_id=request.tool_id,
                    outcome=ToolCallOutcome.DENIED,
                    rule="egress.denied",
                    reason=decision.reason,
                )
            return None
        sandbox = workload.spec.sandbox
        egress_spec = (
            sandbox.egress if sandbox is not None else EgressSpec(mode=EgressMode.RESTRICTED)
        )
        try:
            EgressGuard(egress_spec).check(request.host, request.port)
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


def _is_destructive(action_class: ActionClass | None, tool_trust: ToolTrustLevel) -> bool:
    return action_class is ActionClass.DESTRUCTIVE or tool_trust is ToolTrustLevel.DESTRUCTIVE
