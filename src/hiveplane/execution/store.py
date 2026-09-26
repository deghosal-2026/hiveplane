"""Run storage abstraction and its in-memory implementation."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.event import RunEvent
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import AdmissionResult, DeliveryRecord
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class RunBundle(BaseModel):
    """All persisted state for a single run."""

    model_config = ConfigDict(extra="forbid")

    run: Run
    admission: AdmissionResult | None = None
    events: list[RunEvent] = Field(default_factory=list)
    usage: list[UsageReport] = Field(default_factory=list)
    deliveries: list[DeliveryRecord] = Field(default_factory=list)


class RunStore(Protocol):
    """Storage interface for runs and their recorded history.

    Every method takes an explicit tenant context; reads outside the context's
    tenant look like the run does not exist, and writes that cross a tenant
    boundary raise :class:`~hiveplane.tenancy.TenantScopeError`.
    """

    def save_run(
        self, run: Run, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_run(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Run | None: ...

    def list_runs(
        self,
        *,
        workload: str | None = None,
        state: RunState | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Run]: ...

    def add_event(
        self, event: RunEvent, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_events(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RunEvent]: ...

    def add_usage(
        self, report: UsageReport, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_usage(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[UsageReport]: ...

    def save_admission(
        self, result: AdmissionResult, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_admission(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> AdmissionResult | None: ...

    def add_delivery(
        self, record: DeliveryRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_deliveries(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryRecord]: ...


class _BundleStore:
    """Shared thread-safe bundle storage; subclasses add persistence hooks.

    Bundles are keyed by ``(tenant_id, run_id)`` so the same run id may exist
    independently in two tenants.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bundles: dict[tuple[str, str], RunBundle] = {}

    def _find(self, run_id: str, ctx: TenantContext) -> RunBundle | None:
        if ctx.is_system:
            return next(
                (
                    bundle
                    for (tenant_id, rid), bundle in self._bundles.items()
                    if rid == run_id
                ),
                None,
            )
        return self._bundles.get((ctx.tenant_id, run_id))

    def _require(self, run_id: str, ctx: TenantContext) -> RunBundle:
        bundle = self._find(run_id, ctx)
        if bundle is None:
            raise RunNotFoundError(run_id)
        return bundle

    def _commit(self, bundle: RunBundle) -> None:
        """Persist a bundle after mutation; a no-op in memory."""

    def save_run(self, run: Run, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        with self._lock:
            ctx.require(run.tenant_id)
            key = (run.tenant_id, run.id)
            bundle = self._bundles.get(key)
            if bundle is None:
                bundle = RunBundle(run=run.model_copy(deep=True))
                self._bundles[key] = bundle
            else:
                bundle.run = run.model_copy(deep=True)
            self._commit(bundle)

    def get_run(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> Run | None:
        with self._lock:
            bundle = self._find(run_id, ctx)
            return bundle.run.model_copy(deep=True) if bundle is not None else None

    def list_runs(
        self,
        *,
        workload: str | None = None,
        state: RunState | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Run]:
        with self._lock:
            bundles = [
                bundle
                for (tenant_id, _), bundle in self._bundles.items()
                if ctx.is_system or tenant_id == ctx.tenant_id
            ]
            runs = [bundle.run for bundle in bundles]
        if workload is not None:
            runs = [run for run in runs if run.workload_id == workload]
        if state is not None:
            runs = [run for run in runs if run.state is state]
        runs.sort(key=lambda run: run.created_at)
        return [run.model_copy(deep=True) for run in runs]

    def add_event(self, event: RunEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        with self._lock:
            bundle = self._require(event.run_id, ctx)
            bundle.events.append(event.model_copy(deep=True))
            self._commit(bundle)

    def list_events(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RunEvent]:
        with self._lock:
            events = sorted(self._require(run_id, ctx).events, key=lambda item: item.sequence)
            return [event.model_copy(deep=True) for event in events]

    def add_usage(self, report: UsageReport, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        with self._lock:
            bundle = self._require(report.run_id, ctx)
            bundle.usage.append(report.model_copy(deep=True))
            self._commit(bundle)

    def list_usage(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[UsageReport]:
        with self._lock:
            reports = self._require(run_id, ctx).usage
            return [report.model_copy(deep=True) for report in reports]

    def save_admission(
        self, result: AdmissionResult, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._lock:
            bundle = self._require(result.run_id, ctx)
            bundle.admission = result.model_copy(deep=True)
            self._commit(bundle)

    def get_admission(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> AdmissionResult | None:
        with self._lock:
            admission = self._require(run_id, ctx).admission
            return admission.model_copy(deep=True) if admission is not None else None

    def add_delivery(
        self, record: DeliveryRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._lock:
            bundle = self._require(record.run_id, ctx)
            bundle.deliveries.append(record.model_copy(deep=True))
            self._commit(bundle)

    def list_deliveries(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryRecord]:
        with self._lock:
            deliveries = self._require(run_id, ctx).deliveries
            return [record.model_copy(deep=True) for record in deliveries]


class InMemoryRunStore(_BundleStore):
    """A process-local, thread-safe run store."""


class JsonFileRunStore(_BundleStore):
    """A run store that persists each run bundle to a JSON file.

    Used for local durability and restart tests until the PostgreSQL-backed
    store is available. Files are named ``{tenant_id}__{run_id}.json`` so two
    tenants may hold the same run id.
    """

    def __init__(self, base_dir: str | Path) -> None:
        super().__init__()
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self._dir.glob("*__*.json")):
            bundle = RunBundle.model_validate_json(path.read_text(encoding="utf-8"))
            self._bundles[(bundle.run.tenant_id, bundle.run.id)] = bundle

    def _commit(self, bundle: RunBundle) -> None:
        target = self._dir / f"{bundle.run.tenant_id}__{bundle.run.id}.json"
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_text(bundle.model_dump_json(), encoding="utf-8")
        temp.replace(target)
