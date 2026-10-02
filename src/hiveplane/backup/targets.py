"""Backup targets: adapt control-plane stores to export/restore (M59-03)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from hiveplane.core.run import Run
from hiveplane.registry.models import WorkloadRecord
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext, context_for_run


class RunStoreProtocol(Protocol):
    def list_runs(self, *, ctx: TenantContext = ...) -> list[Run]: ...

    def save_run(self, run: Run, *, ctx: TenantContext = ...) -> None: ...


class RegistryStoreProtocol(Protocol):
    def list_workloads(self, *, ctx: TenantContext = ...) -> list[WorkloadRecord]: ...

    def save_workload(self, record: WorkloadRecord, *, ctx: TenantContext = ...) -> None: ...


@dataclass(frozen=True, slots=True)
class FunctionTarget:
    """A backup target backed by explicit export/restore callables."""

    name: str
    export_fn: Callable[[], list[dict[str, object]]]
    restore_fn: Callable[[list[dict[str, object]]], int]

    def export(self) -> list[dict[str, object]]:
        """Return the target's current records."""
        return self.export_fn()

    def restore(self, rows: list[dict[str, object]]) -> int:
        """Restore the target's records and return the count."""
        return self.restore_fn(rows)


def run_store_target(store: RunStoreProtocol) -> FunctionTarget:
    """Export and restore runs (with their persisted bundles)."""

    def _export() -> list[dict[str, object]]:
        return [run.model_dump(mode="json") for run in store.list_runs(ctx=SYSTEM_CONTEXT)]

    def _restore(rows: list[dict[str, object]]) -> int:
        for row in rows:
            run = Run.model_validate(row)
            store.save_run(run, ctx=context_for_run(run.tenant_id, run.team_id))
        return len(rows)

    return FunctionTarget("runs", _export, _restore)


def registry_store_target(store: RegistryStoreProtocol) -> FunctionTarget:
    """Export and restore registered workloads."""

    def _export() -> list[dict[str, object]]:
        return [
            record.model_dump(mode="json")
            for record in store.list_workloads(ctx=SYSTEM_CONTEXT)
        ]

    def _restore(rows: list[dict[str, object]]) -> int:
        for row in rows:
            record = WorkloadRecord.model_validate(row)
            store.save_workload(record, ctx=context_for_run(record.tenant_id, record.team))
        return len(rows)

    return FunctionTarget("workloads", _export, _restore)
