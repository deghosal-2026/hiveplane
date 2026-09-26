"""Runtime adapters that translate between control-plane concepts and runtimes (M16-M17)."""

from __future__ import annotations

from hiveplane.adapters.base import (
    CONTRACT_VERSION,
    Adapter,
    AdapterCapabilities,
    AdapterEvent,
    AdapterEventKind,
    AdapterRunExecutor,
    buffered_stream,
    capabilities_of,
    conformance_version_of,
    model_identity_of,
    stream_of,
)
from hiveplane.adapters.errors import (
    AdapterError,
    EntrypointLoadError,
    MissingAdapterDependencyError,
    RunCancelledError,
    RunTerminatedError,
    ToolCallBlockedError,
    ToolCallDeniedError,
    ToolCallEscalatedError,
    UnsupportedAdapterError,
    WorkerError,
)
from hiveplane.adapters.graph import CompiledGraph, GraphSnapshot
from hiveplane.adapters.langgraph import LangGraphAdapter
from hiveplane.adapters.loader import Entrypoint, EntrypointLoader
from hiveplane.adapters.openai_agents import OpenAIAgentsAdapter
from hiveplane.adapters.pydanticai import PydanticAIAdapter
from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.adapters.reporter import RunReporter
from hiveplane.adapters.stub import StubAdapter
from hiveplane.adapters.worker import RunControl, WorkerContext

__all__ = [
    "CONTRACT_VERSION",
    "Adapter",
    "AdapterCapabilities",
    "AdapterError",
    "AdapterEvent",
    "AdapterEventKind",
    "AdapterRunExecutor",
    "CompiledGraph",
    "Entrypoint",
    "EntrypointLoadError",
    "EntrypointLoader",
    "GraphSnapshot",
    "LangGraphAdapter",
    "MissingAdapterDependencyError",
    "OpenAIAgentsAdapter",
    "PydanticAIAdapter",
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
    "buffered_stream",
    "capabilities_of",
    "conformance_version_of",
    "model_identity_of",
    "stream_of",
]
