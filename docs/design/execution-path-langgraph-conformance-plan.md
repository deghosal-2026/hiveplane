# LangGraph Adapter & Conformance Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the adapter contract with a real framework — a LangGraph adapter that maps graph supersteps and interrupts onto the run lifecycle — and a conformance suite that fails the build when any adapter violates the contract.

**Architecture:** `LangGraphAdapter` mirrors `RawWorkerAdapter` (background spawner + `RunControl` + `WorkerContext`), driving the compiled graph with `graph.stream(..., stream_mode="values")`, checkpointing cooperatively between supersteps, and mapping a LangGraph `__interrupt__` to run `PAUSED` with `Command(resume=...)` on resume. The conformance suite is a reusable `assert_adapter_conforms(harness)` run against both adapters, plus a negative case that must fail.

**Tech Stack:** Python 3.12, pydantic v2, LangGraph (optional extra), pytest / ruff / mypy strict.

## Global Constraints

- Python `>=3.12`; no new **core** dependencies. `langgraph` is an optional extra only.
- Every task must leave `make check` green: `ruff check`, `mypy src/ tests/`, `pytest --cov` total > 95%.
- Ruff line length 100; `ANN` enforced in `src/`, ignored in `tests/`.
- Mypy strict; `langgraph`/`langchain_core` are third-party and must not be imported at module import time in `src/`.
- All datetimes timezone-aware; injected `clock`.
- No code comments; docstrings in repo style.
- Reuse M16 seams: `Adapter`, `AdapterRunExecutor`, `RunControl`, `WorkerContext`, `RunReporter`, `EntrypointLoader`, `RawWorkerAdapter`, `ToolGateway`, `RunService`.
- Verified LangGraph API (probed against `langchain_core 1.6.3`):
  - `graph.stream(input, config, stream_mode="values") -> Iterator[dict]`
  - an interrupt appears as a chunk containing the `"__interrupt__"` key
  - `graph.get_state(config)` returns a snapshot with `.next: tuple[str, ...]` (non-empty when paused) and `.values: dict`
  - resume is `graph.stream(Command(resume=value), config, stream_mode="values")`
  - nodes receive `config` when annotated `config: RunnableConfig`; values under `config["configurable"]` are passed through.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/hiveplane/adapters/errors.py` | Add `MissingAdapterDependencyError` |
| `src/hiveplane/adapters/loader.py` | Add `load_object()` (resolve `module:attr` without requiring callable) |
| `src/hiveplane/adapters/graph.py` | `CompiledGraph` / `GraphSnapshot` protocols |
| `src/hiveplane/adapters/langgraph.py` | `LangGraphAdapter` |
| `src/hiveplane/adapters/__init__.py` | Export new names |
| `examples/docs_agent.py` | Example compiled graph (interrupt + cooperative checkpoint) |
| `pyproject.toml` | `langgraph` optional extra |
| `.github/workflows/ci.yml` | Install `.[dev,langgraph]` |
| `tests/conformance.py` | `Harness` + `assert_adapter_conforms()` |
| `tests/test_adapter_conformance.py` | Parametrized suite + negative case |
| `docs/ADAPTERS.md`, WBS files | Docs/status |

---

### Task 1: Missing-dependency error and object loader

**Files:**
- Modify: `src/hiveplane/adapters/errors.py`
- Modify: `src/hiveplane/adapters/loader.py`
- Modify: `tests/test_adapter_errors.py`, `tests/test_adapter_loader.py`

**Interfaces:**
- Consumes: existing `AdapterError`, `EntrypointLoadError`.
- Produces:
  - `MissingAdapterDependencyError(adapter: str, reason: str)`
  - `EntrypointLoader.load_object(entrypoint: str) -> object` (resolves `module:attr`; `load()` delegates to it and additionally requires callability)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adapter_errors.py`:

```python
def test_missing_dependency_carries_adapter_and_reason() -> None:
    from hiveplane.adapters.errors import MissingAdapterDependencyError

    exc = MissingAdapterDependencyError("langgraph", "no module named langgraph")
    assert exc.adapter == "langgraph"
    assert exc.reason == "no module named langgraph"
    assert "langgraph" in str(exc)
```

Append to `tests/test_adapter_loader.py`:

```python
def test_load_object_returns_non_callable_attribute(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_graph", "graph = object()\n")
    loaded = EntrypointLoader(root=tmp_path).load_object("worker_graph:graph")
    assert loaded is not None
    assert not callable(loaded)


def test_load_object_can_return_a_callable(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_ok2", "def run(task, ctx):\n    return task\n")
    assert callable(EntrypointLoader(root=tmp_path).load_object("worker_ok2:run"))


def test_load_object_rejects_malformed_entrypoint() -> None:
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader().load_object("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_adapter_errors.py tests/test_adapter_loader.py -q`
Expected: FAIL — `ImportError: MissingAdapterDependencyError` / `AttributeError: 'EntrypointLoader' object has no attribute 'load_object'`

- [ ] **Step 3: Write minimal implementation**

In `src/hiveplane/adapters/errors.py`, after `EntrypointLoadError`:

```python
class MissingAdapterDependencyError(AdapterError):
    """Raised when an adapter's optional runtime dependency is not installed."""

    def __init__(self, adapter: str, reason: str) -> None:
        super().__init__(f"adapter {adapter!r} is unavailable: {reason}")
        self.adapter = adapter
        self.reason = reason
```

Replace `EntrypointLoader.load` in `src/hiveplane/adapters/loader.py` with a shared resolver plus `load_object`:

```python
    def load_object(self, entrypoint: str) -> object:
        """Import and return the attribute named by ``entrypoint``."""
        module_name, _, attr = entrypoint.partition(":")
        if not module_name or not attr:
            raise EntrypointLoadError(entrypoint, "expected 'module:function'")
        with _prepend_sys_path(str(self._root)):
            try:
                module = importlib.import_module(module_name)
            except ImportError as exc:
                raise EntrypointLoadError(entrypoint, str(exc)) from exc
        target = getattr(module, attr, None)
        if target is None:
            raise EntrypointLoadError(entrypoint, f"module has no attribute {attr!r}")
        return target

    def load(self, entrypoint: str) -> Entrypoint:
        """Import and return the callable named by ``entrypoint``."""
        target = self.load_object(entrypoint)
        if not callable(target):
            _, _, attr = entrypoint.partition(":")
            raise EntrypointLoadError(entrypoint, f"attribute {attr!r} is not callable")
        return cast("Entrypoint", target)
```

(The `cast` keeps the existing `no-any-return` fix; `target` is `object`, so cast is still needed. Add `from typing import cast` if not present.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_adapter_errors.py tests/test_adapter_loader.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/errors.py src/hiveplane/adapters/loader.py tests/test_adapter_errors.py tests/test_adapter_loader.py
git commit -m "feat(adapters): add object loader and missing-dependency error"
```

---

### Task 2: Compiled-graph protocols

**Files:**
- Create: `src/hiveplane/adapters/graph.py`
- Test: `tests/test_adapter_graph.py`

**Interfaces:**
- Consumes: stdlib `typing`.
- Produces: `GraphSnapshot` (`.next: tuple[str, ...]`, `.values: dict[str, Any]`) and `CompiledGraph` (`stream(input, config, *, stream_mode) -> Iterator[dict[str, Any]]`, `get_state(config) -> GraphSnapshot`).

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the compiled-graph protocol."""

from __future__ import annotations

from typing import Any
from collections.abc import Iterator

from hiveplane.adapters.graph import CompiledGraph, GraphSnapshot


class _Snapshot:
    @property
    def next(self) -> tuple[str, ...]:
        return ("gate",)

    @property
    def values(self) -> dict[str, Any]:
        return {"note": "a"}


class _Graph:
    def stream(
        self, input: Any, config: dict[str, Any], *, stream_mode: str = "values"
    ) -> Iterator[dict[str, Any]]:
        yield {"note": "a"}

    def get_state(self, config: dict[str, Any]) -> GraphSnapshot:
        return _Snapshot()


def test_protocols_are_runtime_checkable() -> None:
    assert isinstance(_Graph(), CompiledGraph)
    assert isinstance(_Snapshot(), GraphSnapshot)
    assert not isinstance(object(), CompiledGraph)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_graph.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.adapters.graph'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/adapters/graph.py`:

```python
"""The minimal compiled-graph surface the LangGraph adapter depends on (M17).

Keeping this as a Protocol lets the adapter stay typed without importing
langgraph at module import time; the concrete object is duck-typed at runtime.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphSnapshot(Protocol):
    """A point-in-time view of a compiled graph's state."""

    @property
    def next(self) -> tuple[str, ...]:
        """Nodes scheduled to run next; empty when the graph is finished."""
        ...

    @property
    def values(self) -> dict[str, Any]:
        """The current graph state values."""
        ...


@runtime_checkable
class CompiledGraph(Protocol):
    """The subset of a compiled LangGraph the adapter drives."""

    def stream(
        self, input: Any, config: dict[str, Any], *, stream_mode: str = "values"
    ) -> Iterator[dict[str, Any]]:
        """Stream state values as the graph advances."""
        ...

    def get_state(self, config: dict[str, Any]) -> GraphSnapshot:
        """Return the current state snapshot for a thread."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_graph.py -q`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/graph.py tests/test_adapter_graph.py
git commit -m "feat(adapters): add compiled-graph protocols"
```

---

### Task 3: LangGraphAdapter (cooperative lifecycle)

**Files:**
- Create: `src/hiveplane/adapters/langgraph.py`
- Test: `tests/test_adapter_langgraph.py`

**Interfaces:**
- Consumes: `RunControl`/`WorkerContext` (M16), `RunReporter`, `EntrypointLoader.load_object`, `CompiledGraph`/`GraphSnapshot` (Task 2), errors (Task 1), `ToolGateway`, `RuntimeAdapter`, `RunContext`, `IllegalTransitionError`.
- Produces: `LangGraphAdapter(reporter, tools, loader, *, clock=None, spawner=None)` implementing `Adapter`, with an injectable `command_factory` used for resumes.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the LangGraph adapter (fake graph; no langgraph required)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.adapters.errors import UnsupportedAdapterError
from hiveplane.adapters.langgraph import LangGraphAdapter
from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(task: dict[str, Any] | None = None) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.QUEUED,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.SANDBOX,
        task=task or {},
    )


class _Snapshot:
    def __init__(self, next_nodes: tuple[str, ...], values: dict[str, Any]) -> None:
        self._next = next_nodes
        self._values = values

    @property
    def next(self) -> tuple[str, ...]:
        return self._next

    @property
    def values(self) -> dict[str, Any]:
        return self._values


class _Graph:
    def __init__(self, chunks: list[dict[str, Any]], snapshot: _Snapshot) -> None:
        self._chunks = chunks
        self._snapshot = snapshot

    def stream(
        self, input: Any, config: dict[str, Any], *, stream_mode: str = "values"
    ) -> Iterator[dict[str, Any]]:
        yield from self._chunks

    def get_state(self, config: dict[str, Any]) -> _Snapshot:
        return self._snapshot


class _Tools:
    def invoke(self, run_id: str, request: object) -> ToolCallResult:
        return ToolCallResult(run_id=run_id, tool_id="mcp.t.x", outcome=ToolCallOutcome.ALLOWED)


class _Reporter:
    def __init__(self) -> None:
        self.transitions: list[tuple[RunState, str | None]] = []
        self.events: list[EventType] = []
        self.usage: list[UsageReport] = []

    def get(self, run_id: str) -> Run:
        return _run()

    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        detail: str | None = None,
        failure_reason: str | None = None,
        result: object | None = None,
    ) -> Run:
        self.transitions.append((target, failure_reason))
        return _run()

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        self.usage.append(report)
        return _run()

    def record_event(
        self, run_id: str, event_type: EventType, actor: str, *, detail: str | None = None
    ) -> None:
        self.events.append(event_type)


class _Loader:
    def __init__(self, graph: object) -> None:
        self._graph = graph

    def load_object(self, entrypoint: str) -> object:
        return self._graph


def _adapter(graph: object, reporter: _Reporter) -> LangGraphAdapter:
    return LangGraphAdapter(
        reporter,
        _Tools(),  # type: ignore[arg-type]
        _Loader(graph),  # type: ignore[arg-type]
        clock=lambda: _NOW,
        spawner=lambda work: work(),
    )


def _context(workload: AgentWorkload) -> RunContext:
    return RunContext(run=_run(), workload=workload, sandbox=True)


def test_rejects_non_langgraph_manifests(make_manifest: Callable[..., AgentWorkload]) -> None:
    adapter = _adapter(_Graph([], _Snapshot((), {})), _Reporter())
    with pytest.raises(UnsupportedAdapterError):
        adapter.register(make_manifest(adapter="raw-worker"))


def test_completes_a_run_with_final_state(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    graph = _Graph([{"note": "a"}], _Snapshot((), {"note": "done"}))
    reporter = _Reporter()
    adapter = _adapter(graph, reporter)
    adapter.submit(_context(make_manifest(adapter="langgraph")))
    assert reporter.transitions == [(RunState.COMPLETED, None)]
    assert adapter.status("run-1") is RunState.COMPLETED


def test_graph_exception_fails_the_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    class _Boom(_Graph):
        def stream(
            self, input: Any, config: dict[str, Any], *, stream_mode: str = "values"
        ) -> Iterator[dict[str, Any]]:
            raise RuntimeError("kaboom")

    reporter = _Reporter()
    adapter = _adapter(_Boom([], _Snapshot((), {})), reporter)
    adapter.submit(_context(make_manifest(adapter="langgraph")))
    assert reporter.transitions == [(RunState.FAILED, "RuntimeError: kaboom")]


def test_interrupt_pauses_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    graph = _Graph(
        [{"note": "a"}, {"__interrupt__": ("approve?",)}],
        _Snapshot(("gate",), {"note": "a"}),
    )
    reporter = _Reporter()
    adapter = _adapter(graph, reporter)
    adapter.submit(_context(make_manifest(adapter="langgraph")))
    assert reporter.transitions == [(RunState.PAUSED, None)]
    assert adapter.status("run-1") is RunState.PAUSED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_langgraph.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.adapters.langgraph'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/adapters/langgraph.py`:

```python
"""The LangGraph adapter: wraps a compiled graph behind the Adapter contract (M17).

The graph runs on the configured spawner. Supersteps are streamed so the worker
can checkpoint cooperatively (operator pause/resume/cancel), and a LangGraph
``__interrupt__`` maps to a paused run that resumes with ``Command(resume=...)``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from hiveplane.adapters.errors import (
    MissingAdapterDependencyError,
    RunCancelledError,
    ToolCallEscalatedError,
    UnsupportedAdapterError,
    WorkerError,
)
from hiveplane.adapters.graph import CompiledGraph
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.adapters.reporter import RunReporter
from hiveplane.adapters.worker import RunControl, WorkerContext
from hiveplane.core.event import EventType
from hiveplane.core.run import RunState
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.errors import IllegalTransitionError
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallResult, ToolGateway

_INTERRUPT_KEY = "__interrupt__"

Spawner = Callable[[Callable[[], None]], None]


def _require_command() -> Callable[..., Any]:
    """Return LangGraph's ``Command``, or raise if the extra is missing."""
    try:
        from langgraph.types import Command
    except ImportError as exc:
        raise MissingAdapterDependencyError("langgraph", str(exc)) from exc
    return Command


def _thread_spawner(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


def _configurable(run_id: str, ctx: WorkerContext) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id, "hiveplane_ctx": ctx}}


class LangGraphAdapter:
    """Runs a compiled LangGraph and reports through the control-plane boundary."""

    def __init__(
        self,
        reporter: RunReporter,
        tools: ToolGateway,
        loader: EntrypointLoader,
        *,
        clock: Callable[[], datetime] | None = None,
        spawner: Spawner | None = None,
        command_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._reporter = reporter
        self._tools = tools
        self._loader = loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spawner = spawner or _thread_spawner
        self._command_factory = command_factory or _require_command
        self._lock = threading.Lock()
        self._graphs: dict[str, CompiledGraph] = {}
        self._sessions: dict[str, tuple[RunContext, WorkerContext, RunControl]] = {}
        self._states: dict[str, RunState] = {}
        self._usage: dict[str, UsageReport | None] = {}
        self._interrupted: dict[str, bool] = {}

    def register(self, workload: AgentWorkload) -> None:
        """Load and cache a workload's compiled graph, rejecting other adapters."""
        if workload.spec.runtime.adapter is not RuntimeAdapter.LANGGRAPH:
            raise UnsupportedAdapterError(workload.spec.runtime.adapter.value)
        graph = self._loader.load_object(workload.spec.runtime.entrypoint)
        for attr in ("stream", "get_state"):
            if not callable(getattr(graph, attr, None)):
                from hiveplane.adapters.errors import EntrypointLoadError

                raise EntrypointLoadError(
                    workload.spec.runtime.entrypoint, f"graph has no {attr}()"
                )
        self._graphs[workload.name] = graph  # type: ignore[assignment]

    def submit(self, context: RunContext) -> None:
        """Start the graph on the configured spawner and return immediately."""
        graph = self._graph(context.workload)
        control = RunControl()
        ctx = WorkerContext(
            run=context.run,
            workload=context.workload,
            sandbox=context.sandbox,
            tools=self._tools,
            reporter=self._reporter,
            control=control,
            tool_calls=[],
            clock=self._clock,
        )
        with self._lock:
            self._states[context.run.id] = RunState.RUNNING
            self._sessions[context.run.id] = (context, ctx, control)
            self._usage[context.run.id] = None
            self._interrupted[context.run.id] = False
        self._spawner(lambda: self._drive(graph, context, ctx, dict(context.run.task)))

    def pause(self, run_id: str) -> bool:
        """Request a cooperative pause between supersteps."""
        session = self._sessions.get(run_id)
        if session is None:
            return False
        session[2].pause()
        return True

    def resume(self, run_id: str) -> bool:
        """Resume a cooperatively paused run or re-drive an interrupted graph."""
        session = self._sessions.get(run_id)
        if session is None:
            return False
        context, ctx, control = session
        if self._interrupted.get(run_id):
            command = self._command_factory(resume=True)
            graph = self._graph(context.workload)
            with self._lock:
                self._interrupted[run_id] = False
                self._states[run_id] = RunState.RUNNING
            self._spawner(lambda: self._drive(graph, context, ctx, command))
        else:
            control.resume()
        return True

    def cancel(self, run_id: str) -> None:
        """Request cancellation; the run state is owned by the control plane."""
        session = self._sessions.get(run_id)
        if session is not None:
            session[2].cancel()

    def status(self, run_id: str) -> RunState:
        """Return the adapter's last recorded state."""
        return self._states.get(run_id, RunState.QUEUED)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return the last usage report seen for the run."""
        return self._usage.get(run_id)

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        """Return the tool calls the graph routed through the boundary."""
        session = self._sessions.get(run_id)
        return list(session[1]._tool_calls) if session is not None else []  # noqa: SLF001

    def _graph(self, workload: AgentWorkload) -> CompiledGraph:
        graph = self._graphs.get(workload.name)
        if graph is None:
            self.register(workload)
            graph = self._graphs[workload.name]
        return graph

    def _drive(
        self,
        graph: CompiledGraph,
        context: RunContext,
        ctx: WorkerContext,
        payload: Any,
    ) -> None:
        run = context.run
        config = _configurable(run.id, ctx)
        try:
            for chunk in graph.stream(payload, config, stream_mode="values"):
                if _INTERRUPT_KEY in chunk:
                    self._paused(run.id)
                    return
                ctx.checkpoint()
        except RunCancelledError:
            return
        except ToolCallEscalatedError:
            self._reporter.record_event(
                run.id, EventType.OPERATOR_ACTION, "adapter", detail="tool call escalated"
            )
            return
        except WorkerError as exc:
            self._fail(run.id, str(exc))
            return
        except Exception as exc:
            self._fail(run.id, f"{type(exc).__name__}: {exc}")
            return
        snapshot = graph.get_state(config)
        if snapshot.next:
            self._paused(run.id)
            return
        with self._lock:
            self._states[run.id] = RunState.COMPLETED
        self._reporter.transition(
            run.id, RunState.COMPLETED, actor="adapter", result=snapshot.values
        )

    def _paused(self, run_id: str) -> None:
        with self._lock:
            self._interrupted[run_id] = True
            self._states[run_id] = RunState.PAUSED
        self._reporter.transition(run_id, RunState.PAUSED, actor="adapter", detail="interrupted")

    def _fail(self, run_id: str, reason: str) -> None:
        with suppress(IllegalTransitionError):
            self._reporter.transition(
                run_id,
                RunState.FAILED,
                actor="adapter",
                detail=reason,
                failure_reason=reason,
            )
        with self._lock:
            self._states[run_id] = RunState.FAILED
```

Note: `WorkerContext._tool_calls` is private; to avoid reaching into it, add a `tool_calls` property to `WorkerContext` in M16's `worker.py` (Task 3 pre-step): append

```python
    @property
    def tool_calls(self) -> list[ToolCallResult]:
        """The tool calls routed through the boundary so far."""
        return list(self._tool_calls)
```

and change `LangGraphAdapter.tool_calls` to `return list(ctx.tool_calls)`. Also update `RawWorkerAdapter.tool_calls` to return `ctx.tool_calls` for consistency if desired (optional; its own list is equivalent).

The `from ... import EntrypointLoadError` inside `register` must be moved to the module's top-level imports (ruff TID/PLC). Final imports include `EntrypointLoadError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_langgraph.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/hiveplane/adapters/langgraph.py src/hiveplane/adapters/worker.py tests/test_adapter_langgraph.py
git commit -m "feat(adapters): add the LangGraph adapter"
```

---

### Task 4: Example LangGraph workload

**Files:**
- Create: `examples/docs_agent.py`
- Test: `tests/test_adapter_docs_agent.py`

**Interfaces:**
- Consumes: `langgraph`, `langchain_core` (optional extra).
- Produces: `examples.docs_agent.graph` — a compiled graph that calls a tool through `ctx.tool_call`, reports usage, checkpoints cooperatively, and interrupts for review.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the bundled LangGraph example workload."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from hiveplane.adapters.loader import EntrypointLoader  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Ctx:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.model_identity = "openai/gpt-4o/2024-08-06"
        self.tool_calls: list[Any] = []
        self.usage: list[dict[str, Any]] = []
        self.checkpoints = 0

    def tool_call(self, tool_id: str, **kwargs: object) -> Any:
        result = type("R", (), {"tool_id": tool_id, "outcome": "allowed"})()
        self.tool_calls.append(result)
        return result

    def report_usage(self, **kwargs: object) -> None:
        self.usage.append(dict(kwargs))

    def checkpoint(self) -> None:
        self.checkpoints += 1


def test_docs_agent_graph_loads() -> None:
    graph = EntrypointLoader(root=_PROJECT_ROOT).load_object("examples.docs_agent:graph")
    assert callable(getattr(graph, "stream", None))
    assert callable(getattr(graph, "get_state", None))


def test_docs_agent_calls_tool_and_interrupts() -> None:
    graph = EntrypointLoader(root=_PROJECT_ROOT).load_object("examples.docs_agent:graph")
    ctx = _Ctx()
    config = {"configurable": {"thread_id": "run-1", "hiveplane_ctx": ctx}}

    chunks = list(graph.stream({"task": {"issue": "README"}}, config, stream_mode="values"))

    assert any("__interrupt__" in chunk for chunk in chunks)
    assert len(ctx.tool_calls) == 1
    assert len(ctx.usage) == 1
    assert ctx.checkpoints >= 1
    assert graph.get_state(config).next
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_docs_agent.py -q`
Expected: FAIL — `EntrypointLoadError: cannot load entrypoint 'examples.docs_agent:graph'`

- [ ] **Step 3: Write the example graph**

`examples/docs_agent.py`:

```python
"""Example LangGraph workload: plan a docs change, gate on review, finalize.

Demonstrates the LangGraph adapter contract: nodes reach the control-plane
client through ``config['configurable']['hiveplane_ctx']``, call tools through
the boundary, report usage, checkpoint cooperatively, and interrupt for review.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class DocsState(TypedDict, total=False):
    task: dict[str, Any]
    summary: str


def plan(state: DocsState, config: RunnableConfig) -> DocsState:
    """Read the issue through the tool boundary and record usage."""
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.tool_call("mcp.github.read_issue", host="api.github.com", output="{}")
    ctx.report_usage(input_tokens=80, output_tokens=30, tool_calls=1)
    ctx.checkpoint()
    return {"summary": "planned"}


def review_gate(state: DocsState, config: RunnableConfig) -> DocsState:
    """Pause the graph for human review."""
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.checkpoint()
    approved = interrupt({"stage": "review", "summary": state.get("summary")})
    return {"summary": "approved" if approved else "rejected"}


def finalize(state: DocsState) -> DocsState:
    """Finish the draft."""
    return {"summary": f"{state.get('summary', '')}+final"}


_builder = StateGraph(DocsState)
_builder.add_node("plan", plan)
_builder.add_node("review_gate", review_gate)
_builder.add_node("finalize", finalize)
_builder.add_edge(START, "plan")
_builder.add_edge("plan", "review_gate")
_builder.add_edge("review_gate", "finalize")
_builder.add_edge("finalize", END)

graph = _builder.compile(checkpointer=InMemorySaver())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_docs_agent.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add examples/docs_agent.py tests/test_adapter_docs_agent.py
git commit -m "feat(adapters): add the LangGraph docs-agent example"
```

---

### Task 5: Optional dependency and CI

**Files:**
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_adapter_dependency.py`

**Interfaces:**
- Produces: `langgraph` optional extra; CI installs `.[dev,langgraph]`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for optional adapter-dependency declaration."""

from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_langgraph_is_an_optional_extra() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert any("langgraph" in dep for dep in extras["langgraph"])
    core = data["project"]["dependencies"]
    assert not any("langgraph" in dep for dep in core)


def test_ci_installs_the_langgraph_extra() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert ".[dev,langgraph]" in workflow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_dependency.py -q`
Expected: FAIL — `KeyError: 'langgraph'`

- [ ] **Step 3: Write minimal implementation**

In `pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
langgraph = ["langgraph>=0.2"]
```

In `.github/workflows/ci.yml`, change the install run line:

```yaml
          python -m pip install -e ".[dev,langgraph]"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_dependency.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .github/workflows/ci.yml tests/test_adapter_dependency.py
git commit -m "build: add the langgraph optional extra and install it in CI"
```

---

### Task 6: Conformance suite

**Files:**
- Create: `tests/conformance.py`
- Create: `tests/test_adapter_conformance.py`

**Interfaces:**
- Consumes: `Adapter`, `RunService`, `ToolGateway`, `BudgetService`, `Run`, `RunState`, `AdmissionContext`, `InterventionAction`.
- Produces: `Harness` dataclass (`adapter`, `service`, `workload`, `model_identity`, `held`, `release`) and `assert_adapter_conforms(harness)`.

- [ ] **Step 1: Write the conformance harness**

`tests/conformance.py`:

```python
"""Reusable adapter conformance checks (M17, #45).

Any adapter must pass :func:`assert_adapter_conforms`. A violation raises
``AssertionError`` so a deliberately broken adapter fails the build.
"""

from __future__ import annotations

import time
import threading
from collections.abc import Callable
from dataclasses import dataclass

from hiveplane.adapters.base import Adapter
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService


@dataclass
class Harness:
    """Everything needed to run the conformance checks against one adapter."""

    adapter: Adapter
    service: RunService
    workload: str
    model_identity: str
    held: Callable[[str], threading.Event]
    release: Callable[[str], None]


def _wait_for(service: RunService, run_id: str, state: RunState, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.get(run_id).state is state:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"run {run_id!r} did not reach {state.value}; is {service.get(run_id).state.value}"
    )


def assert_adapter_conforms(harness: Harness) -> None:
    """Assert one adapter honours the full lifecycle, usage, and intervention contract."""
    adapter, service, workload, model = (
        harness.adapter,
        harness.service,
        harness.workload,
        harness.model_identity,
    )

    assert adapter.status("missing") is RunState.QUEUED

    run = service.submit(
        workload=workload,
        caller="conformance",
        context=AdmissionContext.SANDBOX,
        model_identity=model,
    )
    service.start(run.id, actor="conformance")
    _wait_for(service, run.id, RunState.COMPLETED)
    completed = service.get(run.id)
    assert completed.cost_usd > 0.0, "usage was not reported and priced"
    assert completed.sandbox is True, "sandbox context was not propagated"
    assert completed.result, "no result recorded"

    paused_run = service.submit(
        workload=workload,
        caller="conformance",
        context=AdmissionContext.SANDBOX,
        task={"hold": True},
        model_identity=model,
    )
    service.start(paused_run.id, actor="conformance")
    assert harness.held(paused_run.id).wait(5.0), "scenario never reached its hold point"

    service.intervene(paused_run.id, InterventionAction.PAUSE, actor="op")
    assert service.get(paused_run.id).state is RunState.PAUSED
    harness.release(paused_run.id)
    _wait_for(service, paused_run.id, RunState.PAUSED)

    service.intervene(paused_run.id, InterventionAction.RESUME, actor="op")
    assert service.get(paused_run.id).state is RunState.RUNNING
    _wait_for(service, paused_run.id, RunState.COMPLETED)

    cancel_run = service.submit(
        workload=workload,
        caller="conformance",
        context=AdmissionContext.SANDBOX,
        task={"hold": True},
        model_identity=model,
    )
    service.start(cancel_run.id, actor="conformance")
    assert harness.held(cancel_run.id).wait(5.0), "scenario never reached its hold point"
    service.intervene(cancel_run.id, InterventionAction.PAUSE, actor="op")
    harness.release(cancel_run.id)
    service.intervene(cancel_run.id, InterventionAction.STOP, actor="op")
    assert service.get(cancel_run.id).state is RunState.CANCELLED
```

- [ ] **Step 2: Write the parametrized suite and negative case**

`tests/test_adapter_conformance.py`:

```python
"""Conformance suite: every adapter must behave identically (M17, #45)."""

from __future__ import annotations

import importlib
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from hiveplane.adapters.base import AdapterRunExecutor
from hiveplane.adapters.langgraph import LangGraphAdapter
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.tools import ToolGateway
from hiveplane.execution.wiring import attach_raw_worker, build_tool_gateway
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Cert, _Sandbox

from conformance import Harness, assert_adapter_conforms

_MODEL = "openai/gpt-4o/2024-08-06"

_RAW_SCENARIO = '''
import threading

_HELD: dict[str, threading.Event] = {}
_RELEASE: dict[str, threading.Event] = {}


def held(run_id):
    return _HELD.setdefault(run_id, threading.Event())


def release(run_id):
    _RELEASE.setdefault(run_id, threading.Event()).set()


def run(task, ctx):
    ctx.tool_call("mcp.t.read", host="api.example.com", output="payload")
    ctx.report_usage(input_tokens=100, output_tokens=50)
    if task.get("hold"):
        _HELD.setdefault(ctx.run_id, threading.Event()).set()
        _RELEASE.setdefault(ctx.run_id, threading.Event()).wait(10.0)
        ctx.checkpoint()
    return {"ok": True}
'''

_LG_SCENARIO = '''
from typing import Any, TypedDict
import threading

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

_HELD: dict[str, threading.Event] = {}
_RELEASE: dict[str, threading.Event] = {}


def held(run_id):
    return _HELD.setdefault(run_id, threading.Event())


def release(run_id):
    _RELEASE.setdefault(run_id, threading.Event()).set()


class S(TypedDict, total=False):
    task: dict[str, Any]
    done: bool


def work(state: S, config: RunnableConfig) -> S:
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.tool_call("mcp.t.read", host="api.example.com", output="payload")
    ctx.report_usage(input_tokens=100, output_tokens=50)
    if (state.get("task") or {}).get("hold"):
        _HELD.setdefault(ctx.run_id, threading.Event()).set()
        _RELEASE.setdefault(ctx.run_id, threading.Event()).wait(10.0)
        ctx.checkpoint()
    return {"done": True}


b = StateGraph(S)
b.add_node("work", work)
b.add_edge(START, "work")
b.add_edge("work", END)
graph = b.compile(checkpointer=InMemorySaver())
'''


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


def _workload(
    make_manifest: Callable[..., AgentWorkload], entrypoint: str, adapter: str = "raw-worker"
) -> AgentWorkload:
    return make_manifest(
        name="agent-1",
        runtime={"adapter": adapter, "entrypoint": entrypoint},
        status="provisional",
        tools={
            "allow": [{"tool_id": "mcp.t.read", "trust_level": "read_only"}],
            "deny": ["mcp.t.denied"],
        },
        output_shaping={"max_bytes": 128, "truncate_strategy": "head"},
        sandbox={
            "enabled": True,
            "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            "egress": {"allow": ["api.example.com"], "mode": "restricted"},
        },
        budget={"per_run_usd": 5.0, "per_day_usd": 50.0, "per_team_usd": 500.0},
    )


def _service(workload: AgentWorkload) -> tuple[RunService, RegistryService, PolicyEngine]:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(workload)
    policy = PolicyEngine(InMemoryPolicyPackStore())
    budget = BudgetService(InMemoryBudgetStore(), CostTable())
    service = RunService(
        InMemoryRunStore(),
        registry,
        admission=AdmissionPipeline(_Cert(True), policy, budget, _Sandbox(True)),
        executor=None,
        fanout=_FanOut(),
        budget=budget,
    )
    return service, registry, policy


def _import_scenario(tmp_path: Path, module_name: str, source: str) -> object:
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        return importlib.import_module(module_name)
    finally:
        sys.path.remove(str(tmp_path))


def _raw_harness(tmp_path: Path, make_manifest: Callable[..., AgentWorkload]) -> Harness:
    module = _import_scenario(tmp_path, "conformance_raw", _RAW_SCENARIO)
    workload = _workload(make_manifest, "conformance_raw:run")
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = attach_raw_worker(service, gateway, root=tmp_path, spawner=None)
    return Harness(
        adapter=adapter,
        service=service,
        workload=workload.name,
        model_identity=_MODEL,
        held=module.held,  # type: ignore[attr-defined]
        release=module.release,  # type: ignore[attr-defined]
    )


def _langgraph_harness(tmp_path: Path, make_manifest: Callable[..., AgentWorkload]) -> Harness:
    module = _import_scenario(tmp_path, "conformance_lg", _LG_SCENARIO)
    workload = _workload(make_manifest, "conformance_lg:graph", adapter="langgraph")
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = LangGraphAdapter(
        service,
        gateway,
        EntrypointLoader(root=tmp_path),
    )
    service.attach_executor(AdapterRunExecutor(adapter))
    return Harness(
        adapter=adapter,
        service=service,
        workload=workload.name,
        model_identity=_MODEL,
        held=module.held,  # type: ignore[attr-defined]
        release=module.release,  # type: ignore[attr-defined]
    )


def test_raw_worker_conforms(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    assert_adapter_conforms(_raw_harness(tmp_path, make_manifest))


def test_langgraph_conforms(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    pytest.importorskip("langgraph")
    assert_adapter_conforms(_langgraph_harness(tmp_path, make_manifest))


class _DeadAdapter:
    """An adapter whose runs never transition; conformance must reject it."""

    def register(self, workload: object) -> None:
        return None

    def submit(self, context: object) -> None:
        return None

    def pause(self, run_id: str) -> bool:
        return True

    def resume(self, run_id: str) -> bool:
        return True

    def cancel(self, run_id: str) -> None:
        return None

    def status(self, run_id: str) -> RunState:
        return RunState.RUNNING

    def usage(self, run_id: str) -> None:
        return None

    def tool_calls(self, run_id: str) -> list[object]:
        return []


def test_broken_adapter_fails_the_suite(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    harness = _raw_harness(tmp_path, make_manifest)
    harness.adapter = _DeadAdapter()  # type: ignore[assignment]
    with pytest.raises(AssertionError):
        assert_adapter_conforms(harness)
```

Note: the conformance suite uses the default thread spawner (`spawner=None`) so pause/resume are exercised concurrently. The `_import_scenario` helper puts the scenario module on `sys.path` only during import; `attach_raw_worker`/`EntrypointLoader` re-add the root when loading, so the entrypoint resolves. `warnings` is unused and must be removed from the imports.

- [ ] **Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_adapter_conformance.py -q`
Expected: PASS (raw-worker always; langgraph when installed). If `langgraph` is not installed, the langgraph test reports SKIPPED.

- [ ] **Step 4: Commit**

```bash
git add tests/conformance.py tests/test_adapter_conformance.py
git commit -m "test(adapters): add the shared conformance suite"
```

---

### Task 7: Exports, docs, and milestone gate

**Files:**
- Modify: `src/hiveplane/adapters/__init__.py`
- Modify: `docs/ADAPTERS.md`
- Modify: `docs/wbs/v0.1.0/wbs-v0.1.0-part8-adapters.md`
- Modify: `docs/wbs/v0.1.0/wbs-v0.1.0-index.md`

- [ ] **Step 1: Export the new names**

Add to `src/hiveplane/adapters/__init__.py` exports (`CompiledGraph`, `GraphSnapshot`, `LangGraphAdapter`, `MissingAdapterDependencyError`) and to `__all__`.

- [ ] **Step 2: Document the LangGraph adapter**

In `docs/ADAPTERS.md`, under **Planned Adapters → v0.1.0**, replace the LangGraph bullet with a short "LangGraph adapter" subsection: optional extra (`pip install -e ".[langgraph]"`), entrypoint resolves to a compiled graph (`module:graph`), nodes read the client from `config["configurable"]["hiveplane_ctx"]`, interrupt → run `PAUSED`, `Command(resume=True)` on resume, cooperative `ctx.checkpoint()` between supersteps.

- [ ] **Step 3: Mark M17 and update the index**

In `wbs-v0.1.0-part8-adapters.md`: check #44 and #45, mark the M17 exit gate boxes with evidence, and update the status line to M16-M17 complete.
In `wbs-v0.1.0-index.md`: update the progress line to 44/60 and remove adapters from the "remain" list.

- [ ] **Step 4: Run the full gate (with and without the extra)**

Run: `.venv/bin/python -m pytest -q --cov=src/hiveplane --cov-report=term` then `.venv/bin/ruff check` then `.venv/bin/mypy src/ tests/`
Expected: all green; coverage > 95%.
Run once with `langgraph` uninstalled to confirm the skip path does not regress coverage:
`.venv/bin/python -m pip uninstall -y langgraph` is not available (uv-managed); instead run with `-k "not langgraph"` and confirm the rest passes.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/__init__.py docs/ADAPTERS.md docs/wbs/v0.1.0/wbs-v0.1.0-part8-adapters.md docs/wbs/v0.1.0/wbs-v0.1.0-index.md
git commit -m "docs(adapters): document LangGraph support and record M17"
```

---

## Self-Review

**Spec coverage (M17 design):**
- Optional extra + CI install → Task 5.
- `CompiledGraph` protocol → Task 2.
- `LangGraphAdapter`, hybrid pause/interrupt → Task 3.
- Example graph → Task 4.
- Conformance suite (register/submit/transition/usage/pause/resume/cancel + sandbox + tool boundary + negative case) → Task 6.
- Loader object resolution + missing-dependency error → Task 1.
- Docs/WBS/exports → Task 7.

**Type consistency:** `EntrypointLoader.load_object` (Task 1) is consumed by `LangGraphAdapter.register` (Task 3) and the docs-agent test (Task 4). `CompiledGraph`/`GraphSnapshot` (Task 2) are used by Task 3 and re-exported in Task 7. `Harness`/`assert_adapter_conforms` (Task 6) are used by `test_adapter_conformance.py`. `WorkerContext.tool_calls` property is added in Task 3 and consumed by `LangGraphAdapter.tool_calls`.

**Known simplifications (documented):** resume uses `Command(resume=True)`; approval values are not threaded through `Adapter.resume` in v0.1.0. Interrupted-run durability across control-plane restart is Part 9 (state store).

**Verification order:** implement Tasks 1-5 first (unit-level), then Task 6 (conformance), then Task 7 (docs/gate). If `langgraph` is unavailable, `pytest.importorskip` skips the LangGraph specs; ensure the rest still clears the 95% coverage gate.