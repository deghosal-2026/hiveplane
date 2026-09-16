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


class RunBundle(BaseModel):
    """All persisted state for a single run."""

    model_config = ConfigDict(extra="forbid")

    run: Run
    admission: AdmissionResult | None = None
    events: list[RunEvent] = Field(default_factory=list)
    usage: list[UsageReport] = Field(default_factory=list)
    deliveries: list[DeliveryRecord] = Field(default_factory=list)


class RunStore(Protocol):
    """Storage interface for runs and their recorded history."""

    def save_run(self, run: Run) -> None: ...

    def get_run(self, run_id: str) -> Run | None: ...

    def list_runs(
        self, *, workload: str | None = None, state: RunState | None = None
    ) -> list[Run]: ...

    def add_event(self, event: RunEvent) -> None: ...

    def list_events(self, run_id: str) -> list[RunEvent]: ...

    def add_usage(self, report: UsageReport) -> None: ...

    def list_usage(self, run_id: str) -> list[UsageReport]: ...

    def save_admission(self, result: AdmissionResult) -> None: ...

    def get_admission(self, run_id: str) -> AdmissionResult | None: ...

    def add_delivery(self, record: DeliveryRecord) -> None: ...

    def list_deliveries(self, run_id: str) -> list[DeliveryRecord]: ...


class _BundleStore:
    """Shared thread-safe bundle storage; subclasses add persistence hooks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bundles: dict[str, RunBundle] = {}

    def _require(self, run_id: str) -> RunBundle:
        bundle = self._bundles.get(run_id)
        if bundle is None:
            raise RunNotFoundError(run_id)
        return bundle

    def _commit(self, run_id: str) -> None:
        """Persist a bundle after mutation; a no-op in memory."""

    def save_run(self, run: Run) -> None:
        with self._lock:
            bundle = self._bundles.get(run.id)
            if bundle is None:
                self._bundles[run.id] = RunBundle(run=run.model_copy(deep=True))
            else:
                bundle.run = run.model_copy(deep=True)
            self._commit(run.id)

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            bundle = self._bundles.get(run_id)
            return bundle.run.model_copy(deep=True) if bundle is not None else None

    def list_runs(
        self, *, workload: str | None = None, state: RunState | None = None
    ) -> list[Run]:
        with self._lock:
            runs = [bundle.run for bundle in self._bundles.values()]
        if workload is not None:
            runs = [run for run in runs if run.workload_id == workload]
        if state is not None:
            runs = [run for run in runs if run.state is state]
        runs.sort(key=lambda run: run.created_at)
        return [run.model_copy(deep=True) for run in runs]

    def add_event(self, event: RunEvent) -> None:
        with self._lock:
            self._require(event.run_id).events.append(event.model_copy(deep=True))
            self._commit(event.run_id)

    def list_events(self, run_id: str) -> list[RunEvent]:
        with self._lock:
            events = sorted(self._require(run_id).events, key=lambda item: item.sequence)
            return [event.model_copy(deep=True) for event in events]

    def add_usage(self, report: UsageReport) -> None:
        with self._lock:
            self._require(report.run_id).usage.append(report.model_copy(deep=True))
            self._commit(report.run_id)

    def list_usage(self, run_id: str) -> list[UsageReport]:
        with self._lock:
            reports = self._require(run_id).usage
            return [report.model_copy(deep=True) for report in reports]

    def save_admission(self, result: AdmissionResult) -> None:
        with self._lock:
            self._require(result.run_id).admission = result.model_copy(deep=True)
            self._commit(result.run_id)

    def get_admission(self, run_id: str) -> AdmissionResult | None:
        with self._lock:
            admission = self._require(run_id).admission
            return admission.model_copy(deep=True) if admission is not None else None

    def add_delivery(self, record: DeliveryRecord) -> None:
        with self._lock:
            self._require(record.run_id).deliveries.append(record.model_copy(deep=True))
            self._commit(record.run_id)

    def list_deliveries(self, run_id: str) -> list[DeliveryRecord]:
        with self._lock:
            deliveries = self._require(run_id).deliveries
            return [record.model_copy(deep=True) for record in deliveries]


class InMemoryRunStore(_BundleStore):
    """A process-local, thread-safe run store."""


class JsonFileRunStore(_BundleStore):
    """A run store that persists each run bundle to a JSON file.

    Used for local durability and restart tests until the PostgreSQL-backed
    store is available.
    """

    def __init__(self, base_dir: str | Path) -> None:
        super().__init__()
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self._dir.glob("*.json")):
            bundle = RunBundle.model_validate_json(path.read_text(encoding="utf-8"))
            self._bundles[bundle.run.id] = bundle

    def _commit(self, run_id: str) -> None:
        bundle = self._bundles[run_id]
        target = self._dir / f"{run_id}.json"
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_text(bundle.model_dump_json(), encoding="utf-8")
        temp.replace(target)
