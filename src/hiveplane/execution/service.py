"""The run service: submission, state machine, intervention, and usage."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from opentelemetry.util.types import AttributeValue
from pydantic import JsonValue

from hiveplane import metrics, telemetry
from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import (
    AdmissionContext,
    Run,
    RunState,
    TriggerOrigin,
    can_transition,
)
from hiveplane.core.usage import UsageReport
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.gates import (
    ApprovalRequests,
    BudgetGate,
    FanOut,
    NullRunExecutor,
    RunExecutor,
    SandboxRuntime,
)
from hiveplane.execution.models import (
    AdmissionOutcome,
    AdmissionResult,
    DeliveryRecord,
    InterventionAction,
    RunContext,
)
from hiveplane.execution.store import RunStore
from hiveplane.execution.story import RunStory, build_run_story
from hiveplane.persistence.audit import AuditLog
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import context_for_run

_TERMINAL = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)


class RunService:
    """Owns run submission, transitions, intervention, and usage accounting."""

    def __init__(
        self,
        store: RunStore,
        registry: RegistryService,
        *,
        admission: AdmissionPipeline,
        executor: RunExecutor | None = None,
        fanout: FanOut,
        approvals: ApprovalRequests | None = None,
        budget: BudgetGate | None = None,
        sandbox_runtime: SandboxRuntime | None = None,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._admission = admission
        self._executor: RunExecutor = executor or NullRunExecutor()
        self._fanout = fanout
        self._approvals = approvals
        self._budget = budget
        self._sandbox_runtime = sandbox_runtime
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"run-{uuid4().hex[:12]}")

    def attach_executor(self, executor: RunExecutor) -> None:
        """Bind the runtime adapter that executes runs after service construction."""
        self._executor = executor

    def reattach(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> bool:
        """Re-attach a recovered run to its executor after a control-plane restart.

        Returns whether the executor rebuilt the bookkeeping needed to resume
        the run. Executors that cannot re-attach report ``False`` and the run is left
        untouched for an operator to reconcile.
        """
        run = self._require(run_id, ctx)
        manifest = self._registry.get(run.workload_id).manifest
        reattach = getattr(self._executor, "reattach", None)
        if reattach is None:
            return False
        return bool(
            reattach(RunContext(run=run, workload=manifest, sandbox=run.sandbox))
        )

    def _require(self, run_id: str, ctx: TenantContext) -> Run:
        run = self._store.get_run(run_id, ctx=ctx)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    @staticmethod
    def _run_ctx(run: Run) -> TenantContext:
        """A trusted internal context scoped to the run's own tenant."""
        return context_for_run(run.tenant_id, run.team_id, run.attribution_key)

    def _append_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        ctx: TenantContext,
        from_state: RunState | None = None,
        to_state: RunState | None = None,
        detail: str | None = None,
    ) -> None:
        sequence = len(self._store.list_events(run_id, ctx=ctx))
        self._store.add_event(
            RunEvent(
                run_id=run_id,
                sequence=sequence,
                type=event_type,
                actor=actor,
                timestamp=self._clock(),
                from_state=from_state,
                to_state=to_state,
                detail=detail,
            ),
            ctx=ctx,
        )

    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, JsonValue] | None = None,
        model_identity: str | None = None,
        trigger_origin: TriggerOrigin | None = None,
        require_approval: bool = False,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Run:
        """Submit a run, admitting or refusing it before it is persisted.

        The run is attributed to the acting tenant context.
        """
        record = self._registry.get(workload)
        now = self._clock()
        run = Run(
            id=self._id_factory(),
            workload_id=workload,
            caller=caller,
            state=RunState.QUEUED,
            model_identity=model_identity,
            created_at=now,
            updated_at=now,
            manifest_version=record.current_version,
            context=context,
            task=task or {},
            trace_id=telemetry.current_trace_id(),
            trigger_origin=trigger_origin,
            tenant_id=ctx.tenant_id,
            team_id=ctx.team_id,
            attribution_key=ctx.attribution_key,
        )
        result = self._admission.check(run, record.manifest, require_approval=require_approval)
        if result.outcome is AdmissionOutcome.REFUSED:
            raise RunAdmissionRefusedError(result)

        if result.sandbox:
            run = run.model_copy(update={"sandbox": True})
        if result.escalation_required:
            run = run.model_copy(update={"state": RunState.PAUSED})

        run_ctx = self._run_ctx(run)
        self._store.save_run(run, ctx=run_ctx)
        self._store.save_admission(result, ctx=run_ctx)
        self._append_event(
            run.id,
            EventType.ADMISSION,
            caller,
            ctx=run_ctx,
            to_state=run.state,
            detail=result.outcome.value,
        )
        if result.escalation_required:
            self._append_event(
                run.id,
                EventType.STATE_CHANGE,
                caller,
                ctx=run_ctx,
                to_state=RunState.PAUSED,
                detail="escalation",
            )
            check = result.policy_check()
            rule = check.rule if check and check.rule else "approvals.required"
            if self._approvals is not None:
                self._approvals.request(
                    run_id=run.id,
                    workload=workload,
                    rule=rule,
                    reason=check.reason if check and check.reason else "approval required",
                )
            self._fanout.notify_escalation(run, record.manifest)
            metrics.get_metrics().record_escalation(
                workload=workload, team=record.team, rule=rule
            )
        metrics.get_metrics().record_run_state(
            workload=workload, team=record.team, state=run.state.value
        )
        return run

    def get(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> Run:
        """Return a run by id within the acting tenant."""
        return self._require(run_id, ctx)

    def start(
        self, run_id: str, *, actor: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Run:
        """Start a queued run (adapter pickup or operator start)."""
        return self.transition(run_id, RunState.RUNNING, actor=actor, ctx=ctx)

    def list_runs(
        self,
        *,
        workload: str | None = None,
        state: RunState | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Run]:
        """List runs, optionally filtered by workload and state."""
        return self._store.list_runs(workload=workload, state=state, ctx=ctx)

    def events(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RunEvent]:
        """Return the ordered event log for a run."""
        self._require(run_id, ctx)
        return self._store.list_events(run_id, ctx=ctx)

    def record_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        detail: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Append an attributed event to a run's history."""
        run = self._require(run_id, ctx)
        self._append_event(
            run_id, event_type, actor, ctx=self._run_ctx(run), detail=detail
        )

    def usage(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[UsageReport]:
        """Return the recorded usage reports for a run."""
        self._require(run_id, ctx)
        return self._store.list_usage(run_id, ctx=ctx)

    def admission(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> AdmissionResult | None:
        """Return the admission result recorded for a run, if any."""
        self._require(run_id, ctx)
        return self._store.get_admission(run_id, ctx=ctx)

    def deliveries(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryRecord]:
        """Return the fan-out delivery attempts recorded for a run."""
        self._require(run_id, ctx)
        return self._store.list_deliveries(run_id, ctx=ctx)

    def story(
        self,
        run_id: str,
        *,
        approvals: Sequence[ApprovalRecord] = (),
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> RunStory:
        """Assemble a run's execution story from its persisted records."""
        run = self._require(run_id, ctx)
        workload = self._registry.get(run.workload_id).manifest
        run_ctx = self._run_ctx(run)
        return build_run_story(
            run=run,
            workload=workload,
            admission=self._store.get_admission(run_id, ctx=run_ctx),
            events=self._store.list_events(run_id, ctx=run_ctx),
            usage=self._store.list_usage(run_id, ctx=run_ctx),
            deliveries=self._store.list_deliveries(run_id, ctx=run_ctx),
            approvals=approvals,
        )

    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        detail: str | None = None,
        failure_reason: str | None = None,
        result: JsonValue | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Run:
        """Move a run to a target state, persisting before side effects."""
        run = self._require(run_id, ctx)
        if not can_transition(run.state, target):
            raise IllegalTransitionError(run_id, run.state, target)
        manifest = self._registry.get(run.workload_id).manifest
        run_ctx = self._run_ctx(run)
        now = self._clock()
        updates: dict[str, object] = {"state": target, "updated_at": now}
        if failure_reason is not None:
            updates["failure_reason"] = failure_reason
        if result is not None:
            updates["result"] = result
        if target is RunState.RUNNING and run.started_at is None:
            updates["started_at"] = now
        if target in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
            updates["finished_at"] = now
        trace_id = telemetry.current_trace_id()
        if trace_id is not None:
            updates["trace_id"] = trace_id
        updated = run.model_copy(update=updates)
        if (
            target is RunState.RUNNING
            and run.state is RunState.QUEUED
            and updated.sandbox
            and self._sandbox_runtime is not None
        ):
            with telemetry.span(
                "sandbox",
                run=updated,
                workload=manifest,
                attributes={"operation": "provision"},
            ) as active:
                instance = self._sandbox_runtime.provision(
                    run_id=run_id,
                    workload=run.workload_id,
                    spec=manifest.spec.sandbox,
                )
                active.set_attribute("sandbox_id", instance.sandbox_id)
            updated = updated.model_copy(update={"sandbox_id": instance.sandbox_id})
        self._store.save_run(updated, ctx=run_ctx)
        self._append_event(
            run_id,
            EventType.STATE_CHANGE,
            actor,
            ctx=run_ctx,
            from_state=run.state,
            to_state=target,
            detail=detail,
        )
        metrics.get_metrics().record_run_state(
            workload=run.workload_id, team=manifest.team, state=target.value
        )
        if target is RunState.RUNNING and run.state is RunState.QUEUED:
            self._executor.start(
                RunContext(run=updated, workload=manifest, sandbox=updated.sandbox)
            )
            if updated.sandbox_id is not None:
                self._append_event(
                    run_id,
                    EventType.SANDBOX,
                    actor,
                    ctx=run_ctx,
                    detail=f"provisioned {updated.sandbox_id}",
                )
        if (
            target in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)
            and updated.sandbox_id is not None
            and self._sandbox_runtime is not None
        ):
            with telemetry.span(
                "sandbox",
                run=updated,
                workload=manifest,
                attributes={
                    "operation": "destroy",
                    "sandbox_id": updated.sandbox_id,
                },
            ):
                self._sandbox_runtime.destroy(updated.sandbox_id)
            self._append_event(
                run_id,
                EventType.SANDBOX,
                actor,
                ctx=run_ctx,
                detail=f"destroyed {updated.sandbox_id}",
            )
        if target in _TERMINAL:
            if target is RunState.COMPLETED and run.context is AdmissionContext.PRODUCTION:
                self._registry.increment_production_runs(run.workload_id)
            self._fanout.notify(updated, manifest)
            self._record_audit(actor, "transition", run_id, detail=target.value, ctx=run_ctx)
            if updated.started_at is not None and updated.finished_at is not None:
                metrics.get_metrics().record_run_duration(
                    workload=run.workload_id,
                    team=manifest.team,
                    seconds=(updated.finished_at - updated.started_at).total_seconds(),
                )
            if target is RunState.FAILED:
                metrics.get_metrics().record_failure(
                    workload=run.workload_id,
                    team=manifest.team,
                    reason=updated.failure_reason or "unknown",
                )
        return updated

    def _record_audit(
        self,
        actor: str,
        action: str,
        run_id: str,
        *,
        detail: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        if self._audit is not None:
            self._audit.append(actor, action, run_id, detail=detail, ctx=ctx)

    def intervene(
        self,
        run_id: str,
        action: InterventionAction,
        *,
        actor: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Run:
        """Apply an operator intervention to a live run."""
        run = self._require(run_id, ctx)
        run_ctx = self._run_ctx(run)
        if action is InterventionAction.PAUSE:
            if run.state is not RunState.RUNNING:
                raise RunNotIntervenableError(run_id, action.value)
            confirmed = self._executor.pause(run_id)
            self._append_event(
                run_id, EventType.OPERATOR_ACTION, actor, ctx=run_ctx, detail=action.value
            )
            if not confirmed:
                return run
            self._record_intervention_latency(run)
            paused = self.transition(run_id, RunState.PAUSED, actor=actor, ctx=ctx)
            self._record_audit(actor, action.value, run_id, ctx=run_ctx)
            return paused
        if action is InterventionAction.RESUME:
            if run.state is not RunState.PAUSED:
                raise RunNotIntervenableError(run_id, action.value)
            # Transition first (#129): a re-driven run may complete inline, and a
            # terminal state must never be followed by a state change.
            resumed = self.transition(run_id, RunState.RUNNING, actor=actor, ctx=ctx)
            self._executor.resume(run_id)
            self._append_event(
                run_id, EventType.OPERATOR_ACTION, actor, ctx=run_ctx, detail=action.value
            )
            self._record_intervention_latency(run)
            self._record_audit(actor, action.value, run_id, ctx=run_ctx)
            return resumed
        self._executor.cancel(run_id)
        self._append_event(
            run_id, EventType.OPERATOR_ACTION, actor, ctx=run_ctx, detail=action.value
        )
        self._record_intervention_latency(run)
        cancelled = self.transition(run_id, RunState.CANCELLED, actor=actor, ctx=ctx)
        self._record_audit(actor, action.value, run_id, ctx=run_ctx)
        return cancelled

    def _record_intervention_latency(self, run: Run) -> None:
        """Record how long the run sat in its last state before an operator acted."""
        metrics.get_metrics().record_intervention_latency(
            workload=run.workload_id,
            seconds=(self._clock() - run.updated_at).total_seconds(),
        )

    def fail(
        self, run_id: str, *, actor: str, reason: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Run:
        """Move a live run to failed with a recorded reason."""
        run = self._require(run_id, ctx)
        if not can_transition(run.state, RunState.FAILED):
            raise IllegalTransitionError(run_id, run.state, RunState.FAILED)
        return self.transition(
            run_id, RunState.FAILED, actor=actor, detail=reason, failure_reason=reason, ctx=ctx
        )

    def record_usage(
        self,
        run_id: str,
        report: UsageReport,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Run:
        """Record usage for a run, enforce budget, and update accumulated cost.

        Usage that arrives after a run is terminal is acknowledged without
        mutating state; pricing happens before anything is persisted, so an
        unpriceable report cannot leave partial state behind.
        """
        run = self._require(run_id, ctx)
        run_ctx = self._run_ctx(run)
        workload = self._registry.get(run.workload_id).manifest
        attributes: dict[str, AttributeValue] = {
            telemetry.MODEL_IDENTITY: run.model_identity
            or report.model_identity
            or "unspecified",
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "cost_usd": report.cost_usd,
        }
        with telemetry.span(
            "model_call", run=run, workload=workload, attributes=attributes
        ) as active:
            if run.state in _TERMINAL:
                self._append_event(
                    run_id,
                    EventType.USAGE,
                    "adapter",
                    ctx=run_ctx,
                    detail=f"late usage ignored (state={run.state.value})",
                )
                active.set_attribute("outcome", "ignored")
                return run
            if report.model_identity is None and run.model_identity is not None:
                report = report.model_copy(update={"model_identity": run.model_identity})
            cost = report.cost_usd
            check = None
            if self._budget is not None:
                outcome = self._budget.record_usage(workload, report)
                cost = outcome.cost_usd
                check = outcome.check
            if cost != report.cost_usd:
                report = report.model_copy(update={"cost_usd": cost})
            self._store.add_usage(report, ctx=run_ctx)
            self._append_event(run_id, EventType.USAGE, "adapter", ctx=run_ctx, detail=str(cost))
            updated = run.model_copy(
                update={"cost_usd": run.cost_usd + cost, "updated_at": self._clock()}
            )
            self._store.save_run(updated, ctx=run_ctx)
            active.set_attribute("cost_usd", cost)
            metrics.get_metrics().record_budget_burn(
                workload=workload.name,
                team=workload.team,
                run_id=run_id,
                cost_usd=updated.cost_usd,
            )
            if check is not None and not check.allowed:
                active.set_attribute("outcome", "budget_exceeded")
                return self.fail(
                    run_id,
                    actor="budget",
                    reason=check.reason or "budget exceeded",
                    ctx=ctx,
                )
            active.set_attribute("outcome", "recorded")
            return updated
