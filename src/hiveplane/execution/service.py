"""The run service: submission, state machine, intervention, and usage."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import JsonValue

from hiveplane import metrics, telemetry
from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import (
    AdmissionContext,
    AgentToolOrigin,
    PipelineOrigin,
    Run,
    RunState,
    TriggerOrigin,
    can_transition,
)
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.defense.guard import DefenseGuard
from hiveplane.defense.scanner import DetectorAction
from hiveplane.defense.taint import TaintRegistry
from hiveplane.events.models import FleetEvent, FleetEventKind
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
    AdmissionCheck,
    AdmissionOutcome,
    AdmissionResult,
    CanaryRouter,
    DeliveryRecord,
    InterventionAction,
    RunContext,
)
from hiveplane.execution.store import RunStore
from hiveplane.execution.story import RunStory, build_run_story
from hiveplane.guards.manager import GuardManager
from hiveplane.guards.models import GuardAction
from hiveplane.persistence.audit import AuditLog
from hiveplane.registry.errors import VersionNotFoundError
from hiveplane.registry.service import RegistryService
from hiveplane.scheduler.models import AdmissionAction, PreemptionRecord, QosClass, QueueEntry
from hiveplane.scheduler.scheduler import Scheduler
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import context_for_run

if TYPE_CHECKING:
    from hiveplane.tenancy.admin import TenantAdminService

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
        eval_hook: Callable[[Run], None] | None = None,
        guards: GuardManager | None = None,
        event_sink: Callable[[FleetEvent], None] | None = None,
        tenant_admin: TenantAdminService | None = None,
        canary: CanaryRouter | None = None,
        taint: TaintRegistry | None = None,
        defense: DefenseGuard | None = None,
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
        self._eval_hook = eval_hook
        self._guards = guards
        self._event_sink = event_sink
        self._tenant_admin = tenant_admin
        self._canary = canary
        self._taint = taint
        self._defense = defense
        self._scheduler: Scheduler | None = None
        self._terminal_hook: Callable[[str], None] | None = None

    def attach_scheduler(self, scheduler: Scheduler | None) -> None:
        """Bind the fleet scheduler so QoS runs can be preempted at checkpoints (M47)."""
        self._scheduler = scheduler

    def checkpoint(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> Run:
        """Mark a running task at an idempotent checkpoint (preemptible)."""
        run = self._require(run_id, ctx)
        if self._scheduler is not None:
            self._scheduler.mark_checkpoint(run_id)
        self._append_event(
            run_id,
            EventType.STATE_CHANGE,
            "scheduler",
            ctx=self._run_ctx(run),
            detail="checkpoint reached (preemptible)",
        )
        return run

    def _register_with_scheduler(
        self, run: Run, *, qos: QosClass, priority: int, ctx: TenantContext
    ) -> None:
        """Register a QoS run with the scheduler; guaranteed runs may preempt."""
        assert self._scheduler is not None
        entry = QueueEntry(
            task_id=run.id,
            tenant_id=run.tenant_id,
            workload=run.workload_id,
            team=run.team_id,
            run_id=run.id,
            qos=qos,
            priority=priority,
            submitted_at=self._clock(),
        )
        decision = self._scheduler.submit(entry)
        if decision.action is not AdmissionAction.QUEUED:
            return
        running = self._scheduler.dequeue()
        if running is None or running.task_id != run.id:
            return
        if qos is QosClass.GUARANTEED:
            record = self._scheduler.preempt(run.id)
            if record is not None:
                self._preempt_victim(record, ctx=ctx)

    def _preempt_victim(self, record: PreemptionRecord, *, ctx: TenantContext) -> None:
        """Cancel a preempted run with attribution and audit the preemption."""
        try:
            victim = self._require(record.victim_task_id, ctx=ctx)
        except Exception:
            return
        detail = f"preempted by guaranteed run {record.preempted_by} at checkpoint"
        if can_transition(victim.state, RunState.CANCELLED):
            self.transition(
                record.victim_task_id, RunState.CANCELLED, actor="scheduler", detail=detail, ctx=ctx
            )
        self._record_audit(
            "scheduler", "scheduler.preempt", record.victim_task_id, detail=detail, ctx=ctx
        )

    def attach_terminal_hook(self, hook: Callable[[str], None] | None) -> None:
        """Bind a best-effort hook run when a run reaches a terminal state.

        Used to revoke a sandbox run's channel token so the boundary can no
        longer be reached after completion/failure/cancellation (#502).
        """
        self._terminal_hook = hook

    def _run_terminal_hook(self, run_id: str) -> None:
        if self._terminal_hook is None:
            return
        try:
            self._terminal_hook(run_id)
        except Exception:
            return

    @property
    def store(self) -> RunStore:
        """Return the run store backing this service."""
        return self._store

    def attach_taint(self, taint: TaintRegistry | None) -> None:
        """Bind the run-scoped taint registry released when a run ends (#543)."""
        self._taint = taint

    def attach_defense(self, defense: DefenseGuard | None) -> None:
        """Bind the defense guard used to scan task input at admission (#538)."""
        self._defense = defense

    def attach_tenant_admin(self, tenant_admin: TenantAdminService | None) -> None:
        """Bind the tenant-lifecycle gate enforced on submission (M58-06)."""
        self._tenant_admin = tenant_admin

    def bind_event_sink(self, sink: Callable[[FleetEvent], None] | None) -> None:
        """Bind the fleet-events sink (best-effort; never blocks a run)."""
        self._event_sink = sink

    def _emit_run_event(self, run: Run) -> None:
        """Publish a terminal run event to subscribers, never raising."""
        if self._event_sink is None:
            return
        event = FleetEvent(
            kind=FleetEventKind.RUN,
            event_type=f"run.{run.state.value}",
            tenant_id=run.tenant_id,
            payload={
                "run_id": run.id,
                "workload": run.workload_id,
                "state": run.state.value,
                "failure_reason": run.failure_reason,
                "cost_usd": run.cost_usd,
            },
            occurred_at=self._clock(),
        )
        try:
            self._event_sink(event)
        except Exception:
            return

    def attach_eval_hook(self, hook: Callable[[Run], None] | None) -> None:
        """Bind the online-eval hook invoked when a production run finishes."""
        self._eval_hook = hook

    def attach_guards(self, guards: GuardManager | None) -> None:
        """Bind the runtime guards (context/velocity) evaluated on usage."""
        self._guards = guards

    def attach_canary(self, canary: CanaryRouter | None) -> None:
        """Bind the canary router that selects/records production-run arms (M38-04)."""
        self._canary = canary

    def attach_executor(self, executor: RunExecutor) -> None:
        """Bind the runtime adapter that executes runs after service construction."""
        self._executor = executor

    def reattach(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> bool:
        """Re-attach a recovered run to its executor after a control-plane restart.

        Returns whether the executor rebuilt the bookkeeping needed to resume
        the run. Executors that cannot re-attach report ``False`` and the run is left
        untouched for an operator to reconcile.
        """
        run = self._require(run_id, ctx)
        manifest = self._registry.get(run.workload_id, ctx=self._run_ctx(run)).manifest
        reattach = getattr(self._executor, "reattach", None)
        if reattach is None:
            return False
        return bool(reattach(RunContext(run=run, workload=manifest, sandbox=run.sandbox)))

    def _require(self, run_id: str, ctx: TenantContext) -> Run:
        run = self._store.get_run(run_id, ctx=ctx)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    @staticmethod
    def _run_ctx(run: Run) -> TenantContext:
        """A trusted internal context scoped to the run's own tenant."""
        return context_for_run(run.tenant_id, run.team_id, run.attribution_key)

    def _scan_task_input(self, run: Run, manifest: AgentWorkload) -> None:
        """Scan the task payload at admission; refuse a deterministic injection.

        Trigger payloads are untrusted by contract and always taint the run;
        operator task input only taints the run when a detector fires.
        """
        assert self._defense is not None
        source = "trigger" if run.trigger_origin is not None else "task"
        text = json.dumps(run.task, default=str)
        result = self._defense.scan_input(
            run_id=run.id,
            workload=manifest.name,
            text=text,
            source_id=source,
            tenant_id=run.tenant_id,
            mark=run.trigger_origin is not None,
        )
        if result.action is not DetectorAction.BLOCK:
            return
        self._defense.mark_untrusted(run_id=run.id, source_id=source, kind=source)
        raise RunAdmissionRefusedError(
            AdmissionResult(
                run_id=run.id,
                workload=manifest.name,
                context=run.context or AdmissionContext.SANDBOX,
                outcome=AdmissionOutcome.REFUSED,
                checks=[
                    AdmissionCheck(
                        step="defense",
                        passed=False,
                        reason="task input contains injection patterns",
                        rule="injection.scan",
                    )
                ],
                refused_reason="task input contains injection patterns",
            )
        )

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
        # The store owns sequence assignment (#504): computing it here required
        # loading the entire event log on every append and raced under
        # concurrency. 0 is a placeholder the store replaces.
        self._store.add_event(
            RunEvent(
                run_id=run_id,
                sequence=0,
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
        pipeline_origin: PipelineOrigin | None = None,
        agent_tool_origin: AgentToolOrigin | None = None,
        require_approval: bool = False,
        shadow_of: str | None = None,
        probe: bool = False,
        read_only: bool = False,
        qos: QosClass | None = None,
        priority: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Run:
        """Submit a run, admitting or refusing it before it is persisted.

        The run is attributed to the acting tenant context. ``shadow_of`` marks a
        shadow run (no fan-out delivery); ``read_only`` hard-blocks side effects.
        A suspended acting tenant is refused before any admission work (M58-06).
        """
        if self._tenant_admin is not None:
            self._tenant_admin.require_active(ctx, ctx.tenant_id)
        record = self._registry.get(workload, ctx=ctx)
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
            pipeline_origin=pipeline_origin,
            agent_tool_origin=agent_tool_origin,
            shadow_of=shadow_of,
            probe=probe,
            read_only=read_only,
            tenant_id=ctx.tenant_id,
            team_id=ctx.team_id,
            attribution_key=ctx.attribution_key,
        )
        if self._defense is not None:
            self._scan_task_input(run, record.manifest)
        result = self._admission.check(run, record.manifest, require_approval=require_approval)
        if result.outcome is AdmissionOutcome.REFUSED:
            raise RunAdmissionRefusedError(result)

        if result.sandbox:
            run = run.model_copy(update={"sandbox": True})
        if result.escalation_required:
            run = run.model_copy(update={"state": RunState.PAUSED})

        if (
            self._canary is not None
            and context is AdmissionContext.PRODUCTION
            and shadow_of is None
            and not probe
        ):
            assignment = self._canary.route(run)
            if assignment is not None:
                run = run.model_copy(
                    update={
                        "canary_rollout_id": assignment.rollout_id,
                        "canary_arm": assignment.arm,
                        "manifest_version": (assignment.candidate_version or run.manifest_version),
                    }
                )

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
                    tenant_id=run.tenant_id,
                    ctx=run_ctx,
                )
            self._fanout.notify_escalation(run, record.manifest)
            metrics.get_metrics().record_escalation(workload=workload, team=record.team, rule=rule)
        metrics.get_metrics().record_run_state(
            workload=workload, team=record.team, state=run.state.value
        )
        if qos is not None and self._scheduler is not None:
            self._register_with_scheduler(run, qos=qos, priority=priority, ctx=run_ctx)
        return run

    def get(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> Run:
        """Return a run by id within the acting tenant."""
        return self._require(run_id, ctx)

    def start(self, run_id: str, *, actor: str, ctx: TenantContext = DEFAULT_CONTEXT) -> Run:
        """Start a queued run (adapter pickup or operator start)."""
        return self.transition(run_id, RunState.RUNNING, actor=actor, ctx=ctx)

    def list_runs(
        self,
        *,
        workload: str | None = None,
        state: RunState | None = None,
        finished_after: datetime | None = None,
        states: tuple[RunState, ...] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Run]:
        """List runs, optionally filtered by workload, state, and window."""
        return self._store.list_runs(
            workload=workload,
            state=state,
            finished_after=finished_after,
            states=states,
            ctx=ctx,
        )

    def events(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[RunEvent]:
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
        self._append_event(run_id, event_type, actor, ctx=self._run_ctx(run), detail=detail)

    def usage(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[UsageReport]:
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
        workload = self._registry.get(run.workload_id, ctx=self._run_ctx(run)).manifest
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
        run_ctx = self._run_ctx(run)
        manifest = self._registry.get(run.workload_id, ctx=run_ctx).manifest
        if run.canary_rollout_id is not None and run.manifest_version is not None:
            with contextlib.suppress(VersionNotFoundError):
                manifest = self._registry.get_version(
                    run.workload_id, run.manifest_version, ctx=run_ctx
                ).manifest
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
            self._run_terminal_hook(run_id)
            if self._scheduler is not None:
                self._scheduler.complete(run_id)
            if self._guards is not None:
                self._guards.release(run_id)
            if self._taint is not None:
                self._taint.clear(run_id)
            if target is RunState.COMPLETED and run.context is AdmissionContext.PRODUCTION:
                self._registry.increment_production_runs(run.workload_id, ctx=run_ctx)
            if updated.shadow_of is None and not updated.probe:
                self._fanout.notify(updated, manifest)
            if (
                self._eval_hook is not None
                and run.context is AdmissionContext.PRODUCTION
                and updated.shadow_of is None
                and not updated.probe
            ):
                self._eval_hook(updated)
            if self._canary is not None and updated.canary_rollout_id is not None:
                with contextlib.suppress(Exception):
                    self._canary.record(updated)
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
            self._emit_run_event(updated)
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
            if not self._executor.resume(run_id):
                # A run held at admission (escalation or gated trigger) never
                # reached an adapter, so there is no session to resume; start it
                # through the executor instead of leaving it running with nothing
                # driving it (#499).
                manifest = self._registry.get(run.workload_id, ctx=run_ctx).manifest
                self._executor.start(
                    RunContext(run=resumed, workload=manifest, sandbox=resumed.sandbox)
                )
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
        workload = self._registry.get(run.workload_id, ctx=run_ctx).manifest
        attributes: dict[str, Any] = {
            telemetry.MODEL_IDENTITY: run.model_identity or report.model_identity or "unspecified",
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
                outcome = self._budget.record_usage(workload, report, ctx=run_ctx)
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
            if self._guards is not None:
                event = self._guards.on_usage(
                    updated,
                    report,
                    budget_remaining_usd=(check.remaining_usd if check is not None else None),
                    ctx=run_ctx,
                )
                if event is not None:
                    self._append_event(
                        run_id,
                        EventType.GUARD,
                        "guard",
                        ctx=run_ctx,
                        detail=f"{event.guard.value}: {event.reason}",
                    )
                    if event.action is GuardAction.PAUSE and can_transition(
                        updated.state, RunState.PAUSED
                    ):
                        self._record_audit(
                            "guard", "guard.pause", run_id, detail=event.reason, ctx=run_ctx
                        )
                        return self.transition(
                            run_id,
                            RunState.PAUSED,
                            actor="guard",
                            detail=event.reason,
                            ctx=ctx,
                        )
            return updated
