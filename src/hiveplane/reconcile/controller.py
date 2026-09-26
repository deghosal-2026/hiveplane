"""The reconciliation controller: observe -> diff -> plan -> act -> record (M26-02).

One pass loads a source's desired state under a single-writer lock, observes
actual state, diffs the two, plans guarded actions, executes them through the
registry, and records the run, drift, and convergence state. Plan mode renders
the same plan without mutating anything.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.fleet.reconcile import (
    DesiredSpec,
    DriftResolution,
    ReconcileState,
    ReconcileStatus,
    SpecKind,
    SpecSource,
)
from hiveplane.reconcile.conflict import ConflictPolicy
from hiveplane.reconcile.differ import Differ, DiffResult
from hiveplane.reconcile.executor import ActionExecutor
from hiveplane.reconcile.loader import DesiredStateLoader, LoaderError
from hiveplane.reconcile.locking import ReconcileLock
from hiveplane.reconcile.models import (
    ActionResult,
    ActionStatus,
    ReconcileAction,
    ReconcileMode,
    ReconcileOutcome,
    ReconcileRun,
    ReconcileStatusView,
)
from hiveplane.reconcile.observe import ObservedState, Observer
from hiveplane.reconcile.planner import Guardrails, Planner
from hiveplane.reconcile.source import SourceRef
from hiveplane.reconcile.store import ReconcileStore
from hiveplane.tenancy import Role, TenantContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class ReconcileController:
    """Converges actual fleet state to a source's declared desired state."""

    def __init__(
        self,
        *,
        store: ReconcileStore,
        observer: Observer,
        executor: ActionExecutor,
        loader: DesiredStateLoader | None = None,
        lock: ReconcileLock | None = None,
        guardrails: Guardrails | None = None,
        policy: ConflictPolicy | None = None,
        differ: Differ | None = None,
        planner: Planner | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._observer = observer
        self._executor = executor
        self._loader = loader or DesiredStateLoader()
        self._lock = lock
        self._differ = differ or Differ(policy)
        self._planner = planner or Planner(guardrails)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"rr-{uuid.uuid4().hex[:12]}")

    def reconcile(
        self,
        source: SourceRef,
        *,
        mode: ReconcileMode = ReconcileMode.APPLY,
        confirmed: bool = False,
        tenant_id: str = DEFAULT_TENANT_ID,
        secret_resolver: Callable[[str], str] | None = None,
    ) -> ReconcileRun:
        """Run one reconcile pass over ``source`` and return its record."""
        run_id = self._id_factory()
        started_at = self._clock()
        if mode is ReconcileMode.APPLY and self._lock is not None:
            with self._lock.hold(source.source_id) as acquired:
                if not acquired:
                    return self._blocked_run(run_id, source, started_at, mode)
                return self._run_pass(
                    source,
                    run_id=run_id,
                    mode=mode,
                    confirmed=confirmed,
                    tenant_id=tenant_id,
                    started_at=started_at,
                    secret_resolver=secret_resolver,
                )
        return self._run_pass(
            source,
            run_id=run_id,
            mode=mode,
            confirmed=confirmed,
            tenant_id=tenant_id,
            started_at=started_at,
            secret_resolver=secret_resolver,
        )

    def _run_pass(
        self,
        source: SourceRef,
        *,
        run_id: str,
        mode: ReconcileMode,
        confirmed: bool,
        tenant_id: str,
        started_at: datetime,
        secret_resolver: Callable[[str], str] | None,
    ) -> ReconcileRun:
        ctx = TenantContext(tenant_id=tenant_id, role=Role.ADMIN)
        try:
            desired = source.load(self._loader, secret_resolver=secret_resolver)
        except LoaderError as exc:
            return self._error_run(run_id, source, started_at, mode, str(exc))

        previous = self._store.list_specs(source.source_id, ctx=ctx)
        previous_managed = {
            spec.name for spec in previous if spec.kind is SpecKind.WORKLOAD
        }
        other_managed = {
            spec.name
            for spec in self._store.list_specs(ctx=ctx)
            if spec.source_id != source.source_id and spec.kind is SpecKind.WORKLOAD
        }
        observed = self._observer.snapshot(ctx=ctx)
        state = self._store.get_state(source.source_id, ctx=ctx)
        diff = self._differ.diff(
            desired,
            observed,
            run_id=run_id,
            tenant_id=tenant_id,
            detected_at=started_at,
            previous_managed=previous_managed,
            other_managed=other_managed,
        )
        plan = self._planner.plan(
            diff,
            source_id=source.source_id,
            revision=desired.revision,
            mode=mode,
            created_at=started_at,
            desired_count=len(desired.specs),
            first_run=state is None,
            confirmed=confirmed,
        )
        if mode is ReconcileMode.PLAN:
            return ReconcileRun(
                run_id=run_id,
                tenant_id=tenant_id,
                source_id=source.source_id,
                source=desired.source,
                revision=desired.revision,
                mode=mode,
                started_at=started_at,
                finished_at=self._clock(),
                outcome=(
                    ReconcileOutcome.IN_SYNC if plan.in_sync else ReconcileOutcome.PLANNED
                ),
                actions=[_planned_result(action) for action in plan.actions],
            )

        results = [self._executor.execute(action, diff) for action in plan.actions]
        outcome = _outcome(plan.in_sync, results)
        run = ReconcileRun(
            run_id=run_id,
            tenant_id=tenant_id,
            source_id=source.source_id,
            source=desired.source,
            revision=desired.revision,
            mode=mode,
            started_at=started_at,
            finished_at=self._clock(),
            outcome=outcome,
            actions=results,
        )
        self._record(
            source,
            desired.specs,
            desired.revision,
            desired.source,
            observed,
            diff,
            results,
            run,
            ctx,
        )
        return run

    def _record(
        self,
        source: SourceRef,
        specs: list[DesiredSpec],
        revision: str,
        spec_source: SpecSource,
        observed: ObservedState,
        diff: DiffResult,
        results: list[ActionResult],
        run: ReconcileRun,
        ctx: TenantContext,
    ) -> None:
        self._store.replace_source_specs(source.source_id, specs, ctx=ctx)
        for drift in diff.drifts:
            self._store.add_drift(drift, ctx=ctx)
        self._store.save_state(
            ReconcileState(
                source_id=source.source_id,
                tenant_id=ctx.tenant_id,
                source=spec_source,
                last_revision=revision,
                last_observed_hash=_observed_hash(observed),
                last_reconcile_at=self._clock(),
                status=_state_status(diff, results),
            ),
            ctx=ctx,
        )
        self._store.add_run(run, ctx=ctx)

    def status(
        self, source_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> ReconcileStatusView:
        """Return a source's convergence status for API/CLI display."""
        ctx = TenantContext(tenant_id=tenant_id, role=Role.ADMIN)
        state = self._store.get_state(source_id, ctx=ctx)
        runs = self._store.list_runs(source_id, limit=1, ctx=ctx)
        open_drift = [
            drift
            for drift in self._store.list_drift(ctx=ctx)
            if drift.resolution is DriftResolution.UNRESOLVED
        ]
        last_run = runs[-1] if runs else None
        return ReconcileStatusView(
            source_id=source_id,
            source=state.source if state is not None else SpecSource.GIT,
            status=state.status if state is not None else ReconcileStatus.PENDING,
            last_revision=state.last_revision if state is not None else None,
            last_reconcile_at=state.last_reconcile_at if state is not None else None,
            open_drift=len(open_drift),
            last_run_id=last_run.run_id if last_run is not None else None,
            last_outcome=last_run.outcome if last_run is not None else None,
        )

    def _blocked_run(
        self, run_id: str, source: SourceRef, started_at: datetime, mode: ReconcileMode
    ) -> ReconcileRun:
        return ReconcileRun(
            run_id=run_id,
            source_id=source.source_id,
            source=SpecSource.GIT,
            revision="unknown",
            mode=mode,
            started_at=started_at,
            finished_at=self._clock(),
            outcome=ReconcileOutcome.BLOCKED,
            error="another controller holds the lock for this source",
        )

    def _error_run(
        self,
        run_id: str,
        source: SourceRef,
        started_at: datetime,
        mode: ReconcileMode,
        error: str,
    ) -> ReconcileRun:
        return ReconcileRun(
            run_id=run_id,
            source_id=source.source_id,
            source=SpecSource.GIT,
            revision="unknown",
            mode=mode,
            started_at=started_at,
            finished_at=self._clock(),
            outcome=ReconcileOutcome.ERROR,
            error=error,
        )


def _planned_result(action: ReconcileAction) -> ActionResult:
    return ActionResult(
        action_id=action.action_id,
        kind=action.kind,
        object_kind=action.object_kind,
        object_ref=action.object_ref,
        status=ActionStatus.BLOCKED if action.blocked else ActionStatus.PLANNED,
        detail=action.block_reason,
    )


def _outcome(in_sync: bool, results: list[ActionResult]) -> ReconcileOutcome:
    if any(result.status is ActionStatus.FAILED for result in results):
        return ReconcileOutcome.ERROR
    if in_sync:
        return ReconcileOutcome.IN_SYNC
    if any(result.status is ActionStatus.APPLIED for result in results):
        return ReconcileOutcome.APPLIED
    if any(result.status is ActionStatus.BLOCKED for result in results):
        return ReconcileOutcome.BLOCKED
    return ReconcileOutcome.IN_SYNC


def _state_status(diff: DiffResult, results: list[ActionResult]) -> ReconcileStatus:
    if any(result.status is ActionStatus.FAILED for result in results):
        return ReconcileStatus.ERROR
    if any(result.status is ActionStatus.BLOCKED for result in results):
        return ReconcileStatus.DRIFTED
    if any(drift.resolution is DriftResolution.UNRESOLVED for drift in diff.drifts):
        return ReconcileStatus.DRIFTED
    return ReconcileStatus.IN_SYNC


def _observed_hash(observed: ObservedState) -> str:
    payload = {
        "workloads": observed.workloads,
        "policy_versions": observed.policy_versions,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
