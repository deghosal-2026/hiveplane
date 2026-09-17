"""Runtime adapters that translate between control-plane concepts and runtimes (M16-M17)."""

from __future__ import annotations

from hiveplane.adapters.base import Adapter, AdapterRunExecutor
from hiveplane.adapters.errors import (
    AdapterError,
    EntrypointLoadError,
    RunCancelledError,
    RunTerminatedError,
    ToolCallBlockedError,
    ToolCallDeniedError,
    ToolCallEscalatedError,
    UnsupportedAdapterError,
    WorkerError,
)
from hiveplane.adapters.loader import Entrypoint, EntrypointLoader
from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.adapters.reporter import RunReporter
from hiveplane.adapters.stub import StubAdapter
from hiveplane.adapters.worker import RunControl, WorkerContext

__all__ = [
    "Adapter",
    "AdapterError",
    "AdapterRunExecutor",
    "Entrypoint",
    "EntrypointLoadError",
    "EntrypointLoader",
    "RawWorkerAdapter",
    "RunCancelledError",
    "RunControl",
    "RunReporter",
    "RunTerminatedError",
    "StubAdapter",
    "ToolCallBlockedError",
    "ToolCallDeniedError",
    "ToolCallEscalatedError",
    "UnsupportedAdapterError",
    "WorkerContext",
    "WorkerError",
]
