"""Contract v2 path tests: helpers, dispatch, and adapter failure paths (M31)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from conformance import Harness
from hiveplane.adapters.base import (
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
from hiveplane.adapters.dispatch import DispatchingAdapter
from hiveplane.adapters.errors import (
    EntrypointLoadError,
    UnsupportedAdapterError,
)
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.adapters.openai_agents import OpenAIAgentsAdapter
from hiveplane.adapters.pydanticai import PydanticAIAdapter
from hiveplane.adapters.stub import StubAdapter
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.wiring import build_tool_gateway
from test_adapter_conformance import _MODEL, _import_scenario, _service, _workload

_FAILING = '''
def build(model):
    raise ValueError("boom")
'''

_CANCELLING = '''
from hiveplane.core.run import RunState
from hiveplane.adapters.errors import RunCancelledError


def build(model):
    raise RunCancelledError(RunState.CANCELLED)
'''

_NOT_AN_AGENT = "value = 42\n"

_ESCALATED = '''
from types import SimpleNamespace

from hiveplane.adapters.errors import ToolCallEscalatedError


def build(model):
    raise ToolCallEscalatedError(SimpleNamespace(tool_id="mcp.t.read"))
'''

_WORKER_ERROR = '''
from hiveplane.adapters.errors import WorkerError


def build(model):
    raise WorkerError("worker boom")
'''


# --------------------------------------------------------------------------- #
# base helpers
# --------------------------------------------------------------------------- #
def test_helpers_default_for_pre_v2_objects() -> None:
    class PreV2:
        def status(self, run_id: str) -> RunState:
            return RunState.QUEUED

    assert capabilities_of(PreV2()).contract_version == "1"
    assert conformance_version_of(PreV2()) == "1"
    assert model_identity_of(PreV2(), "run") is None
    assert next(iter(stream_of(PreV2(), "run"))).state is RunState.QUEUED
    assert next(iter(stream_of(object(), "run"))).state is RunState.QUEUED


def test_buffered_stream_terminal_and_live() -> None:
    live = list(buffered_stream("r", RunState.RUNNING))
    assert [event.kind for event in live] == [AdapterEventKind.STATE]
    done = list(buffered_stream("r", RunState.COMPLETED, model_identity="m", cost_usd=1.0))
    assert [event.kind for event in done] == [
        AdapterEventKind.STATE,
        AdapterEventKind.COMPLETED,
    ]
    assert done[1].model_identity == "m"


def test_adapter_run_executor_v2_passthroughs() -> None:
    adapter = StubAdapter()
    executor = AdapterRunExecutor(adapter)
    assert executor.capabilities().contract_version == "2"
    assert executor.conformance_version() == "2"
    assert executor.model_identity("missing") is None
    events = list(executor.stream("missing"))
    assert events and events[0].kind is AdapterEventKind.STATE
    assert executor.reattach(object()) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# dispatch v2
# --------------------------------------------------------------------------- #
class _SpyAdapter:
    def __init__(self, *, streaming: bool = False, version: str = "2") -> None:
        self._streaming = streaming
        self._version = version
        self.cancelled: list[tuple[str, float | None]] = []

    def cancel(self, run_id: str, *, deadline_s: float | None = None) -> None:
        self.cancelled.append((run_id, deadline_s))

    def submit(self, context: object) -> None:
        return None

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(streaming=self._streaming, pause_resume=True)

    def conformance_version(self) -> str:
        return self._version

    def stream(self, run_id: str) -> object:
        return iter([AdapterEvent(kind=AdapterEventKind.STATE, run_id=run_id)])

    def model_identity(self, run_id: str) -> str | None:
        return "spy/model/1"


def test_dispatch_v2_aggregates_and_routes() -> None:
    raw = _SpyAdapter(streaming=True)
    graph = _SpyAdapter()
    dispatcher = DispatchingAdapter(
        {
            RuntimeAdapter.RAW_WORKER: cast(Adapter, raw),
            RuntimeAdapter.LANGGRAPH: cast(Adapter, graph),
        }
    )
    assert dispatcher.capabilities().streaming is True
    assert dispatcher.conformance_version() == "2"
    dispatcher.submit(_ctx("run-1", RuntimeAdapter.RAW_WORKER))
    assert dispatcher.model_identity("run-1") == "spy/model/1"
    assert next(iter(dispatcher.stream("run-1"))).run_id == "run-1"
    dispatcher.cancel("run-1", deadline_s=2.0)
    assert raw.cancelled == [("run-1", 2.0)]


def test_dispatch_mixed_versions_defaults_to_v2() -> None:
    dispatcher = DispatchingAdapter(
        {
            RuntimeAdapter.RAW_WORKER: cast(Adapter, _SpyAdapter(version="2")),
            RuntimeAdapter.LANGGRAPH: cast(Adapter, _SpyAdapter(version="1")),
        }
    )
    assert dispatcher.conformance_version() == "2"


def test_dispatch_unknown_owner_and_adapter() -> None:
    dispatcher = DispatchingAdapter(
        {RuntimeAdapter.RAW_WORKER: cast(Adapter, _SpyAdapter())}
    )
    with pytest.raises(UnsupportedAdapterError):
        dispatcher.status("nope")
    with pytest.raises(UnsupportedAdapterError):
        dispatcher.submit(_ctx("run-2", RuntimeAdapter.LANGGRAPH))


# --------------------------------------------------------------------------- #
# adapter failure paths
# --------------------------------------------------------------------------- #
def _adapter(cls: type, loader: EntrypointLoader) -> object:
    return cls(object(), object(), loader)


@pytest.mark.parametrize("cls", [PydanticAIAdapter, OpenAIAgentsAdapter])
def test_register_rejects_other_adapter(
    cls: type, make_manifest: Callable[..., AgentWorkload]
) -> None:
    workload = _workload(make_manifest, "m:run", adapter="raw-worker")
    adapter = _adapter(cls, EntrypointLoader(root="."))
    with pytest.raises(UnsupportedAdapterError):
        adapter.register(workload)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "cls", [PydanticAIAdapter, OpenAIAgentsAdapter]
)
def test_register_rejects_non_agent_entrypoint(
    cls: type, make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    _, name = _import_scenario(tmp_path, "not_agent", _NOT_AN_AGENT)
    adapter_name = "pydanticai" if cls is PydanticAIAdapter else "openai-agents"
    workload = _workload(make_manifest, f"{name}:value", adapter=adapter_name)
    adapter = _adapter(cls, EntrypointLoader(root=tmp_path))
    with pytest.raises(EntrypointLoadError):
        adapter.register(workload)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("cls", "adapter_name"),
    [(PydanticAIAdapter, "pydanticai"), (OpenAIAgentsAdapter, "openai-agents")],
)
def test_adapter_failure_paths(
    cls: type,
    adapter_name: str,
    make_manifest: Callable[..., AgentWorkload],
    tmp_path: Path,
) -> None:
    _, name = _import_scenario(tmp_path, f"fail_{adapter_name}", _FAILING)
    workload = _workload(make_manifest, f"{name}:build", adapter=adapter_name)
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = cls(service, gateway, EntrypointLoader(root=tmp_path))
    service.attach_executor(AdapterRunExecutor(adapter))

    assert adapter.usage("missing") is None
    assert adapter.model_identity("missing") is None
    assert adapter.tool_calls("missing") == []
    assert next(iter(adapter.stream("missing"))).kind is AdapterEventKind.STATE

    run = service.submit(
        workload=workload.name,
        caller="test",
        context=AdmissionContext.SANDBOX,
        model_identity=_MODEL,
    )
    service.start(run.id, actor="test")
    _wait_for(service, run.id, RunState.FAILED)
    assert adapter.status(run.id) is RunState.FAILED


@pytest.mark.parametrize(
    ("cls", "adapter_name"),
    [(PydanticAIAdapter, "pydanticai"), (OpenAIAgentsAdapter, "openai-agents")],
)
def test_adapter_cancellation_path(
    cls: type,
    adapter_name: str,
    make_manifest: Callable[..., AgentWorkload],
    tmp_path: Path,
) -> None:
    _, name = _import_scenario(tmp_path, f"cancel_{adapter_name}", _CANCELLING)
    workload = _workload(make_manifest, f"{name}:build", adapter=adapter_name)
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = cls(service, gateway, EntrypointLoader(root=tmp_path))
    service.attach_executor(AdapterRunExecutor(adapter))
    run = service.submit(
        workload=workload.name,
        caller="test",
        context=AdmissionContext.SANDBOX,
        model_identity=_MODEL,
    )
    service.start(run.id, actor="test")
    # The entrypoint raises RunCancelledError; the adapter leaves state untouched.
    time.sleep(0.05)
    assert adapter.status(run.id) is RunState.RUNNING
    adapter.cancel(run.id, deadline_s=1.0)


def _ctx(run_id: str, adapter: RuntimeAdapter) -> RunContext:
    from types import SimpleNamespace

    return cast(
        RunContext,
        SimpleNamespace(
        run=SimpleNamespace(id=run_id),
        workload=SimpleNamespace(spec=SimpleNamespace(runtime=SimpleNamespace(adapter=adapter))),
        ),
    )


def _wait_for(service: object, run_id: str, state: RunState, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.get(run_id).state is state:  # type: ignore[attr-defined]
            return
        time.sleep(0.01)
    raise AssertionError(f"run did not reach {state.value}")


def _wait_adapter(adapter: object, run_id: str, state: RunState, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if adapter.status(run_id) is state:  # type: ignore[attr-defined]
            return
        time.sleep(0.01)
    raise AssertionError(f"adapter did not reach {state.value}")


def test_harness_type_is_exported() -> None:
    assert Harness is not None


def test_prompt_and_flatten_helpers() -> None:
    from types import SimpleNamespace

    from hiveplane.adapters.openai_agents import _flatten as oa_flatten
    from hiveplane.adapters.openai_agents import _prompt_from_task as oa_prompt
    from hiveplane.adapters.pydanticai import _flatten as pai_flatten
    from hiveplane.adapters.pydanticai import _prompt_from_task as pai_prompt

    assert pai_prompt({}) == ""
    assert pai_prompt({"prompt": "hi"}) == "hi"
    assert pai_prompt({"other": 1}) == '{"other": 1}'
    assert pai_flatten([SimpleNamespace(parts=[SimpleNamespace(content="x")])]) == "x"
    assert pai_flatten([SimpleNamespace(parts=[SimpleNamespace(content=1)])]) == " "

    assert oa_flatten("hi") == "hi"
    assert oa_flatten([{"content": "a"}]) == "a"
    assert oa_flatten([{"content": [{"text": "b"}]}]) == "b"
    assert oa_flatten([SimpleNamespace(content="c")]) == "c"
    assert oa_flatten(["d"]) == "d"
    assert oa_flatten([]) == " "
    assert oa_prompt({}) == ""
    assert oa_prompt({"message": "m"}) == "m"


def test_require_raises_when_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    import hiveplane.adapters.openai_agents as oa
    import hiveplane.adapters.pydanticai as pai
    from hiveplane.adapters.errors import MissingAdapterDependencyError

    monkeypatch.setitem(sys.modules, "pydantic_ai", None)
    with pytest.raises(MissingAdapterDependencyError):
        pai._require_pydantic_ai()
    monkeypatch.setitem(sys.modules, "agents", None)
    with pytest.raises(MissingAdapterDependencyError):
        oa._require_openai_agents()


def test_governed_model_classes_are_cached() -> None:
    from hiveplane.adapters.openai_agents import governed_model_class as oa_model
    from hiveplane.adapters.pydanticai import governed_model_class as pai_model

    assert pai_model() is pai_model()
    assert oa_model() is oa_model()


def test_pause_resume_are_refused() -> None:
    for cls in (PydanticAIAdapter, OpenAIAgentsAdapter):
        adapter = _adapter(cls, EntrypointLoader(root="."))
        assert adapter.pause("x") is False  # type: ignore[attr-defined]
        assert adapter.resume("x") is False  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("cls", "adapter_name", "source", "expected"),
    [
        (PydanticAIAdapter, "pydanticai", _ESCALATED, RunState.PAUSED),
        (PydanticAIAdapter, "pydanticai", _WORKER_ERROR, RunState.FAILED),
        (OpenAIAgentsAdapter, "openai-agents", _ESCALATED, RunState.PAUSED),
        (OpenAIAgentsAdapter, "openai-agents", _WORKER_ERROR, RunState.FAILED),
    ],
)
def test_adapter_escalation_and_worker_error(
    cls: type,
    adapter_name: str,
    source: str,
    expected: RunState,
    make_manifest: Callable[..., AgentWorkload],
    tmp_path: Path,
) -> None:
    _, name = _import_scenario(tmp_path, f"branch_{adapter_name}_{expected.value}", source)
    workload = _workload(make_manifest, f"{name}:build", adapter=adapter_name)
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = cls(service, gateway, EntrypointLoader(root=tmp_path))
    service.attach_executor(AdapterRunExecutor(adapter))
    run = service.submit(
        workload=workload.name,
        caller="test",
        context=AdmissionContext.SANDBOX,
        model_identity=_MODEL,
    )
    service.start(run.id, actor="test")
    _wait_adapter(adapter, run.id, expected)
    assert adapter.status(run.id) is expected


def test_dispatch_lifecycle_passthroughs() -> None:
    stub = StubAdapter()
    dispatcher = DispatchingAdapter({RuntimeAdapter.RAW_WORKER: cast(Adapter, stub)})
    dispatcher.submit(_ctx("run-3", RuntimeAdapter.RAW_WORKER))
    assert dispatcher.pause("run-3") is True
    assert dispatcher.resume("run-3") is True
    assert dispatcher.status("run-3") is RunState.RUNNING
    assert dispatcher.usage("run-3") is None
    assert dispatcher.tool_calls("run-3") == []
