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
from hiveplane.core.run import AdmissionContext, Run, RunState, can_transition
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

    def _require(self, run_id: str) -> Run:
        run = self._store.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    def _append_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        from_state: RunState | None = None,
        to_state: RunState | None = None,
        detail: str | None = None,
    ) -> None:
        sequence = len(self._store.list_events(run_id))
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
            )
        )

    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, JsonValue] | None = None,
        model_identity: str | None = None,
    ) -> Run:
        """Submit a run, admitting or refusing it before it is persisted."""
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
        )
        result = self._admission.check(run, record.manifest)
        if result.outcome is AdmissionOutcome.REFUSED:
            raise RunAdmissionRefusedError(result)

        if result.sandbox:
            run = run.model_copy(update={"sandbox": True})
        if result.escalation_required:
            run = run.model_copy(update={"state": RunState.PAUSED})

        self._store.save_run(run)
        self._store.save_admission(result)
        self._append_event(
            run.id,
            EventType.ADMISSION,
            caller,
            to_state=run.state,
            detail=result.outcome.value,
        )
        if result.escalation_required:
            self._append_event(
                run.id,
                EventType.STATE_CHANGE,
                caller,
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

    def get(self, run_id: str) -> Run:
        """Return a run by id."""
        return self._require(run_id)

    def start(self, run_id: str, *, actor: str) -> Run:
        """Start a queued run (adapter pickup or operator start)."""
        return self.transition(run_id, RunState.RUNNING, actor=actor)

    def list_runs(
        self, *, workload: str | None = None, state: RunState | None = None
    ) -> list[Run]:
        """List runs, optionally filtered by workload and state."""
        return self._store.list_runs(workload=workload, state=state)

    def events(self, run_id: str) -> list[RunEvent]:
        """Return the ordered event log for a run."""
        self._require(run_id)
        return self._store.list_events(run_id)

    def record_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        detail: str | None = None,
    ) -> None:
        """Append an attributed event to a run's history."""
        self._require(run_id)
        self._append_event(run_id, event_type, actor, detail=detail)

    def usage(self, run_id: str) -> list[UsageReport]:
        """Return the recorded usage reports for a run."""
        self._require(run_id)
        return self._store.list_usage(run_id)

    def admission(self, run_id: str) -> AdmissionResult | None:
        """Return the admission result recorded for a run, if any."""
        self._require(run_id)
        return self._store.get_admission(run_id)

    def deliveries(self, run_id: str) -> list[DeliveryRecord]:
        """Return the fan-out delivery attempts recorded for a run."""
        self._require(run_id)
        return self._store.list_deliveries(run_id)

    def story(self, run_id: str, *, approvals: Sequence[ApprovalRecord] = ()) -> RunStory:
        """Assemble a run's execution story from its persisted records."""
        run = self._require(run_id)
        workload = self._registry.get(run.workload_id).manifest
        return build_run_story(
            run=run,
            workload=workload,
            admission=self._store.get_admission(run_id),
            events=self._store.list_events(run_id),
            usage=self._store.list_usage(run_id),
            deliveries=self._store.list_deliveries(run_id),
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
    ) -> Run:
        """Move a run to a target state, persisting before side effects."""
        run = self._require(run_id)
        if not can_transition(run.state, target):
            raise IllegalTransitionError(run_id, run.state, target)
        manifest = self._registry.get(run.workload_id).manifest
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
        self._store.save_run(updated)
        self._append_event(
            run_id,
            EventType.STATE_CHANGE,
            actor,
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
                detail=f"destroyed {updated.sandbox_id}",
            )
        if target in _TERMINAL:
            if target is RunState.COMPLETED and run.context is AdmissionContext.PRODUCTION:
                self._registry.increment_production_runs(run.workload_id)
            self._fanout.notify(updated, manifest)
            self._record_audit(actor, "transition", run_id, detail=target.value)
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
        self, actor: str, action: str, run_id: str, *, detail: str | None = None
    ) -> None:
        if self._audit is not None:
            self._audit.append(actor, action, run_id, detail=detail)

    def intervene(self, run_id: str, action: InterventionAction, *, actor: str) -> Run:
        """Apply an operator intervention to a live run."""
        run = self._require(run_id)
        if action is InterventionAction.PAUSE:
            if run.state is not RunState.RUNNING:
                raise RunNotIntervenableError(run_id, action.value)
            confirmed = self._executor.pause(run_id)
            self._append_event(run_id, EventType.OPERATOR_ACTION, actor, detail=action.value)
            if not confirmed:
                return run
            self._record_intervention_latency(run)
            paused = self.transition(run_id, RunState.PAUSED, actor=actor)
            self._record_audit(actor, action.value, run_id)
            return paused
        if action is InterventionAction.RESUME:
            if run.state is not RunState.PAUSED:
                raise RunNotIntervenableError(run_id, action.value)
            self._executor.resume(run_id)
            self._append_event(run_id, EventType.OPERATOR_ACTION, actor, detail=action.value)
            self._record_intervention_latency(run)
            resumed = self.transition(run_id, RunState.RUNNING, actor=actor)
            self._record_audit(actor, action.value, run_id)
            return resumed
        self._executor.cancel(run_id)
        self._append_event(run_id, EventType.OPERATOR_ACTION, actor, detail=action.value)
        self._record_intervention_latency(run)
        cancelled = self.transition(run_id, RunState.CANCELLED, actor=actor)
        self._record_audit(actor, action.value, run_id)
        return cancelled

    def _record_intervention_latency(self, run: Run) -> None:
        """Record how long the run sat in its last state before an operator acted."""
        metrics.get_metrics().record_intervention_latency(
            workload=run.workload_id,
            seconds=(self._clock() - run.updated_at).total_seconds(),
        )

    def fail(self, run_id: str, *, actor: str, reason: str) -> Run:
        """Move a live run to failed with a recorded reason."""
        run = self._require(run_id)
        if not can_transition(run.state, RunState.FAILED):
            raise IllegalTransitionError(run_id, run.state, RunState.FAILED)
        return self.transition(
            run_id, RunState.FAILED, actor=actor, detail=reason, failure_reason=reason
        )

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        """Record usage for a run, enforce budget, and update accumulated cost.

        Usage that arrives after a run is terminal is acknowledged without
        mutating state; pricing happens before anything is persisted, so an
        unpriceable report cannot leave partial state behind.
        """
        run = self._require(run_id)
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
            self._store.add_usage(report)
            self._append_event(run_id, EventType.USAGE, "adapter", detail=str(cost))
            updated = run.model_copy(
                update={"cost_usd": run.cost_usd + cost, "updated_at": self._clock()}
            )
            self._store.save_run(updated)
            active.set_attribute("cost_usd", cost)
            metrics.get_metrics().record_budget_burn(
                workload=workload.name,
                team=workload.team,
                run_id=run_id,
                cost_usd=updated.cost_usd,
            )
            if check is not None and not check.allowed:
                active.set_attribute("outcome", "budget_exceeded")
                return self.fail(run_id, actor="budget", reason=check.reason or "budget exceeded")
            active.set_attribute("outcome", "recorded")
            return updated
