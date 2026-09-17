# Adapters & Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a real raw-Python workload run through the full control-plane lifecycle (admission → running → usage → tool calls → terminal), with the worker reporting outcomes and routing every tool call through the existing policy/egress/shaping boundary.

**Architecture:** An adapter is a `RunExecutor` that executes a workload entrypoint on a background thread using an injected `WorkerContext` client. The client reports usage/state back through a `RunReporter` seam (structurally satisfied by `RunService`) and routes tool calls through `ToolGateway`. Wiring builds the service first, then binds the adapter via `RunService.attach_executor`.

**Tech Stack:** Python 3.12, pydantic v2, `threading`, `importlib`, pytest / ruff / mypy strict.

## Global Constraints

- Python `>=3.12`; pydantic v2; no new runtime dependencies.
- Every task must leave `make check` green: `ruff check`, `mypy src/ tests/`, `pytest --cov` total > 95%.
- Ruff line length 100; `ANN` enforced in `src/` (annotate every function), ignored in `tests/`.
- Mypy strict; tests may omit annotations.
- All datetimes are timezone-aware; logic takes an injected `clock`, never the wall clock.
- Do not add code comments; keep module/class/function docstrings in the repo's style.
- Reuse existing models: `RunState`/`Run`, `UsageReport`, `EventType`, `ToolCallRequest`/`ToolCallResult`/`ToolCallOutcome`/`ToolGateway`, `AgentWorkload`, `RuntimeAdapter`, `RunContext`.
- Nothing framework-specific leaks into `core/` or `execution/`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/hiveplane/adapters/errors.py` | Adapter + worker error types |
| `src/hiveplane/adapters/reporter.py` | `RunReporter` protocol (control-plane reporting seam) |
| `src/hiveplane/adapters/worker.py` | `RunControl` (cooperative pause/cancel) + `WorkerContext` |
| `src/hiveplane/adapters/loader.py` | `EntrypointLoader` (`module:function` resolution) |
| `src/hiveplane/adapters/raw_worker.py` | `RawWorkerAdapter` implementing `Adapter` |
| `src/hiveplane/execution/service.py` | `attach_executor`, optional executor, `transition(result=...)` |
| `src/hiveplane/execution/wiring.py` | `build_tool_gateway`, `attach_raw_worker` |
| `src/hiveplane/config.py` | `ExecutionSettings.entrypoints_root` |
| `src/hiveplane/api/app.py` | Use the new wiring functions |
| `examples/repo_agent.py` | Reference raw-worker entrypoint |
| `docs/ADAPTERS.md` | Worker contract + usage docs |

All test files live flat in `tests/`, matching the repo convention.

---

### Task 1: Adapter errors

**Files:**
- Create: `src/hiveplane/adapters/errors.py`
- Test: `tests/test_adapter_errors.py`

**Interfaces:**
- Consumes: `RunState` (`core/run.py`), `ToolCallResult` (`execution/tools.py`).
- Produces: `AdapterError`, `UnsupportedAdapterError`, `EntrypointLoadError`, `WorkerError`, `ToolCallDeniedError`, `ToolCallBlockedError`, `ToolCallEscalatedError`, `RunTerminatedError`, `RunCancelledError`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for adapter and worker errors."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.adapters.errors import (
    AdapterError,
    EntrypointLoadError,
    RunCancelledError,
    RunTerminatedError,
    ToolCallDeniedError,
    UnsupportedAdapterError,
    WorkerError,
)
from hiveplane.core.run import RunState
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult


def _result(outcome: ToolCallOutcome = ToolCallOutcome.DENIED) -> ToolCallResult:
    return ToolCallResult(run_id="run-1", tool_id="mcp.t.x", outcome=outcome, reason="nope")


def test_errors_share_a_base() -> None:
    assert issubclass(WorkerError, AdapterError)
    assert issubclass(UnsupportedAdapterError, AdapterError)
    assert issubclass(EntrypointLoadError, AdapterError)


def test_unsupported_adapter_carries_name() -> None:
    exc = UnsupportedAdapterError("langgraph")
    assert exc.adapter == "langgraph"
    assert "langgraph" in str(exc)


def test_entrypoint_load_error_carries_context() -> None:
    exc = EntrypointLoadError("a.b:c", "no module named a.b")
    assert exc.entrypoint == "a.b:c"
    assert exc.reason == "no module named a.b"


def test_tool_call_denied_carries_result() -> None:
    exc = ToolCallDeniedError(_result())
    assert exc.result.tool_id == "mcp.t.x"


def test_run_terminated_carries_state() -> None:
    assert RunTerminatedError(RunState.FAILED).state is RunState.FAILED


def test_run_cancelled_carries_state() -> None:
    assert RunCancelledError(RunState.CANCELLED).state is RunState.CANCELLED


def test_error_is_an_exception() -> None:
    assert isinstance(RunTerminatedError(RunState.FAILED), Exception)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_errors.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.adapters.errors'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/adapters/errors.py`:

```python
"""Adapter and worker domain errors (M16)."""

from __future__ import annotations

from hiveplane.core.run import RunState
from hiveplane.execution.tools import ToolCallResult


class AdapterError(Exception):
    """Base class for runtime adapter errors."""


class UnsupportedAdapterError(AdapterError):
    """Raised when an adapter is asked to run a workload it does not support."""

    def __init__(self, adapter: str) -> None:
        super().__init__(f"adapter {adapter!r} is not supported by this runtime")
        self.adapter = adapter


class EntrypointLoadError(AdapterError):
    """Raised when a workload entrypoint cannot be resolved or imported."""

    def __init__(self, entrypoint: str, reason: str) -> None:
        super().__init__(f"cannot load entrypoint {entrypoint!r}: {reason}")
        self.entrypoint = entrypoint
        self.reason = reason


class WorkerError(AdapterError):
    """Base class for errors a worker raises through the control plane."""


class ToolCallDeniedError(WorkerError):
    """Raised when the tool boundary denies a tool call."""

    def __init__(self, result: ToolCallResult) -> None:
        super().__init__(f"tool call {result.tool_id!r} denied: {result.reason}")
        self.result = result


class ToolCallBlockedError(WorkerError):
    """Raised when the tool boundary blocks a tool call for injection."""

    def __init__(self, result: ToolCallResult) -> None:
        super().__init__(f"tool call {result.tool_id!r} blocked: {result.reason}")
        self.result = result


class ToolCallEscalatedError(WorkerError):
    """Raised when a tool call requires approval and the run is paused."""

    def __init__(self, result: ToolCallResult) -> None:
        super().__init__(f"tool call {result.tool_id!r} requires approval")
        self.result = result


class RunTerminatedError(WorkerError):
    """Raised when the run reached a terminal state during a report."""

    def __init__(self, state: RunState) -> None:
        super().__init__(f"run is {state.value}")
        self.state = state


class RunCancelledError(WorkerError):
    """Raised at a checkpoint when the run has been cancelled."""

    def __init__(self, state: RunState = RunState.CANCELLED) -> None:
        super().__init__(f"run is {state.value}")
        self.state = state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_errors.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/errors.py tests/test_adapter_errors.py
git commit -m "feat(adapters): add adapter and worker errors"
```

---

### Task 2: RunReporter protocol

**Files:**
- Create: `src/hiveplane/adapters/reporter.py`
- Test: `tests/test_adapter_reporter.py`

**Interfaces:**
- Consumes: `Run`, `RunState`, `UsageReport`, `EventType`, `JsonValue`.
- Produces: `RunReporter` (`@runtime_checkable` Protocol) with `get`, `transition`, `record_usage`, `record_event`. This mirrors the public `RunService` methods.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the adapter reporting seam."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.adapters.reporter import RunReporter
from hiveplane.core.event import EventType
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport


class _Reporter:
    def get(self, run_id: str) -> Run:
        raise NotImplementedError

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
        raise NotImplementedError

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        raise NotImplementedError

    def record_event(
        self, run_id: str, event_type: EventType, actor: str, *, detail: str | None = None
    ) -> None:
        raise NotImplementedError


def test_reporter_protocol_is_runtime_checkable() -> None:
    assert isinstance(_Reporter(), RunReporter)


def test_incomplete_object_is_not_a_reporter() -> None:
    assert not isinstance(object(), RunReporter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_reporter.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.adapters.reporter'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/adapters/reporter.py`:

```python
"""The reporting seam adapters use to tell the control plane what happened.

Adapters report; they do not decide. ``RunService`` satisfies this protocol
structurally, so wiring passes the service itself as the reporter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from hiveplane.core.event import EventType
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport


@runtime_checkable
class RunReporter(Protocol):
    """The control-plane operations a runtime adapter may report through."""

    def get(self, run_id: str) -> Run:
        """Return the current run aggregate."""
        ...

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
        """Move a run to a target state, recording the transition."""
        ...

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        """Record priced usage for a run and enforce budget."""
        ...

    def record_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        detail: str | None = None,
    ) -> None:
        """Append an attributed event to a run's history."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_reporter.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/reporter.py tests/test_adapter_reporter.py
git commit -m "feat(adapters): add the RunReporter reporting protocol"
```

---

### Task 3: RunControl (cooperative pause/cancel)

**Files:**
- Create: `src/hiveplane/adapters/worker.py`
- Test: `tests/test_adapter_control.py`

**Interfaces:**
- Consumes: `RunCancelledError` (Task 1), `RunState`.
- Produces: `RunControl` with `pause()`, `resume()`, `cancel()`, `paused`, `cancelled`, `checkpoint()`. `checkpoint()` blocks while paused and raises `RunCancelledError` when cancelled.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for cooperative run control."""

from __future__ import annotations

import threading

import pytest

from hiveplane.adapters.errors import RunCancelledError
from hiveplane.adapters.worker import RunControl


def test_checkpoint_returns_when_running() -> None:
    RunControl().checkpoint()


def test_checkpoint_raises_when_cancelled() -> None:
    control = RunControl()
    control.cancel()
    assert control.cancelled is True
    with pytest.raises(RunCancelledError):
        control.checkpoint()


def test_checkpoint_blocks_while_paused_then_resumes() -> None:
    control = RunControl()
    control.pause()
    paused_before = control.paused
    assert paused_before is True
    reached = threading.Event()

    def _worker() -> None:
        control.checkpoint()
        reached.set()

    thread = threading.Thread(target=_worker)
    thread.start()
    assert reached.wait(0.05) is False

    control.resume()
    paused_after = control.paused
    assert paused_after is False
    assert reached.wait(1.0) is True
    thread.join()


def test_cancel_releases_a_paused_checkpoint() -> None:
    control = RunControl()
    control.pause()
    outcome: list[str] = []

    def _worker() -> None:
        try:
            control.checkpoint()
        except RunCancelledError:
            outcome.append("cancelled")

    thread = threading.Thread(target=_worker)
    thread.start()
    control.cancel()
    thread.join(1.0)
    assert outcome == ["cancelled"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_control.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.adapters.worker'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/adapters/worker.py`:

```python
"""The worker-facing control-plane client (M16).

A workload entrypoint receives a :class:`WorkerContext` that routes tool calls
through the policy boundary, reports usage, and offers cooperative pause/cancel
checkpoints. Workers report; the control plane decides.
"""

from __future__ import annotations

import threading

from hiveplane.adapters.errors import RunCancelledError
from hiveplane.core.run import RunState


class RunControl:
    """Cooperative pause/cancel signaling for a live run."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._paused = False
        self._cancelled = False

    @property
    def paused(self) -> bool:
        """Return True while the run is paused."""
        with self._condition:
            return self._paused

    @property
    def cancelled(self) -> bool:
        """Return True once the run has been cancelled."""
        with self._condition:
            return self._cancelled

    def pause(self) -> None:
        """Request a cooperative pause."""
        with self._condition:
            self._paused = True

    def resume(self) -> None:
        """Clear a pause request and wake any blocked checkpoint."""
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def cancel(self) -> None:
        """Request cancellation and wake any blocked checkpoint."""
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def checkpoint(self) -> None:
        """Block while paused, raising when the run has been cancelled."""
        with self._condition:
            while self._paused and not self._cancelled:
                self._condition.wait()
            if self._cancelled:
                raise RunCancelledError(RunState.CANCELLED)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_control.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/worker.py tests/test_adapter_control.py
git commit -m "feat(adapters): add cooperative run control"
```

---

### Task 4: WorkerContext

**Files:**
- Modify: `src/hiveplane/adapters/worker.py`
- Test: `tests/test_adapter_worker_context.py`

**Interfaces:**
- Consumes: `RunControl` (Task 3), `RunReporter` (Task 2), `ToolGateway`/`ToolCallRequest`/`ToolCallResult`/`ToolCallOutcome`, `Run`, `AgentWorkload`, `UsageReport`, `DataSensitivity`, `ActionClass`.
- Produces: `WorkerContext(run, workload, sandbox, tools, reporter, control, tool_calls, clock)` with properties `run_id`, `workload`, `task`, `sandbox`, `model_identity`; methods `tool_call(...)`, `report_usage(...)`, `checkpoint()`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the worker context client."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.adapters.errors import (
    RunCancelledError,
    RunTerminatedError,
    ToolCallDeniedError,
    ToolCallEscalatedError,
)
from hiveplane.adapters.worker import RunControl, WorkerContext
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(state: RunState = RunState.RUNNING) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=state,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.SANDBOX,
        task={"repo": "hiveplane"},
    )


class _Tools:
    def __init__(self, outcome: ToolCallOutcome) -> None:
        self._outcome = outcome
        self.requests: list[object] = []

    def invoke(self, run_id: str, request: object) -> ToolCallResult:
        self.requests.append(request)
        return ToolCallResult(run_id=run_id, tool_id="mcp.t.x", outcome=self._outcome)


class _Reporter:
    def __init__(self, state: RunState = RunState.RUNNING) -> None:
        self._state = state
        self.usage: list[UsageReport] = []

    def get(self, run_id: str) -> Run:
        return _run(self._state)

    def transition(self, run_id: str, target: RunState, **kwargs: object) -> Run:
        return _run(target)

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        self.usage.append(report)
        return _run(self._state)

    def record_event(self, run_id: str, event_type: object, actor: str, **kwargs: object) -> None:
        return None


def _context(
    make_manifest: Callable[..., AgentWorkload],
    *,
    tools: _Tools,
    reporter: _Reporter,
    control: RunControl | None = None,
    tool_calls: list[ToolCallResult] | None = None,
) -> WorkerContext:
    return WorkerContext(
        run=_run(),
        workload=make_manifest(),
        sandbox=True,
        tools=tools,  # type: ignore[arg-type]
        reporter=reporter,
        control=control or RunControl(),
        tool_calls=tool_calls if tool_calls is not None else [],
        clock=lambda: _NOW,
    )


def test_context_exposes_run_metadata(make_manifest: Callable[..., AgentWorkload]) -> None:
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.ALLOWED), reporter=_Reporter())
    assert ctx.run_id == "run-1"
    assert ctx.workload == "agent-1"
    assert ctx.sandbox is True
    assert ctx.model_identity == "openai/gpt-4o/2024-08-06"
    assert ctx.task == {"repo": "hiveplane"}


def test_allowed_tool_call_is_recorded(make_manifest: Callable[..., AgentWorkload]) -> None:
    calls: list[ToolCallResult] = []
    ctx = _context(
        make_manifest,
        tools=_Tools(ToolCallOutcome.ALLOWED),
        reporter=_Reporter(),
        tool_calls=calls,
    )
    result = ctx.tool_call("mcp.t.x")
    assert result.outcome is ToolCallOutcome.ALLOWED
    assert calls == [result]


def test_denied_tool_call_raises(make_manifest: Callable[..., AgentWorkload]) -> None:
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.DENIED), reporter=_Reporter())
    with pytest.raises(ToolCallDeniedError):
        ctx.tool_call("mcp.t.x")


def test_escalated_tool_call_raises(make_manifest: Callable[..., AgentWorkload]) -> None:
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.ESCALATED), reporter=_Reporter())
    with pytest.raises(ToolCallEscalatedError):
        ctx.tool_call("mcp.t.x")


def test_report_usage_prices_and_forwards(make_manifest: Callable[..., AgentWorkload]) -> None:
    reporter = _Reporter()
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.ALLOWED), reporter=reporter)
    ctx.report_usage(input_tokens=10, output_tokens=5, tool_calls=1, cost_usd=0.01)
    assert len(reporter.usage) == 1
    assert reporter.usage[0].input_tokens == 10
    assert reporter.usage[0].model_identity == "openai/gpt-4o/2024-08-06"


def test_report_usage_raises_when_terminal(make_manifest: Callable[..., AgentWorkload]) -> None:
    reporter = _Reporter(state=RunState.FAILED)
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.ALLOWED), reporter=reporter)
    with pytest.raises(RunTerminatedError):
        ctx.report_usage(cost_usd=0.01)


def test_checkpoint_raises_when_cancelled(make_manifest: Callable[..., AgentWorkload]) -> None:
    control = RunControl()
    control.cancel()
    ctx = _context(
        make_manifest, tools=_Tools(ToolCallOutcome.ALLOWED), reporter=_Reporter(), control=control
    )
    with pytest.raises(RunCancelledError):
        ctx.checkpoint()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_worker_context.py -q`
Expected: FAIL with `ImportError: cannot import name 'WorkerContext' from 'hiveplane.adapters.worker'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/hiveplane/adapters/worker.py`. Replace the Task 3 imports at the top of the module with the merged block below (ruff isort expects a single `errors` import), then append the class:

```python
import threading
from collections.abc import Callable
from datetime import datetime

from pydantic import JsonValue

from hiveplane.adapters.errors import (
    RunCancelledError,
    RunTerminatedError,
    ToolCallBlockedError,
    ToolCallDeniedError,
    ToolCallEscalatedError,
)
from hiveplane.adapters.reporter import RunReporter
from hiveplane.core.decision import ActionClass, DataSensitivity
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.tools import (
    ToolCallOutcome,
    ToolCallRequest,
    ToolCallResult,
    ToolGateway,
)

_TERMINAL = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)
```

```python
class WorkerContext:
    """The control-plane client handed to a workload entrypoint."""

    def __init__(
        self,
        *,
        run: Run,
        workload: AgentWorkload,
        sandbox: bool,
        tools: ToolGateway,
        reporter: RunReporter,
        control: RunControl,
        tool_calls: list[ToolCallResult],
        clock: Callable[[], datetime],
    ) -> None:
        self._run = run
        self._workload = workload
        self._sandbox = sandbox
        self._tools = tools
        self._reporter = reporter
        self._control = control
        self._tool_calls = tool_calls
        self._clock = clock

    @property
    def run_id(self) -> str:
        """The id of the run being executed."""
        return self._run.id

    @property
    def workload(self) -> str:
        """The name of the workload being executed."""
        return self._workload.name

    @property
    def task(self) -> dict[str, JsonValue]:
        """The submitted task payload."""
        return dict(self._run.task)

    @property
    def sandbox(self) -> bool:
        """Whether the run is executing in a sandbox context."""
        return self._sandbox

    @property
    def model_identity(self) -> str | None:
        """The model identity bound to the run."""
        return self._run.model_identity

    def tool_call(
        self,
        tool_id: str,
        *,
        action_class: ActionClass | None = None,
        data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL,
        host: str | None = None,
        output: str | None = None,
    ) -> ToolCallResult:
        """Route a tool call through the policy, egress, and shaping boundary."""
        result = self._tools.invoke(
            self._run.id,
            ToolCallRequest(
                tool_id=tool_id,
                action_class=action_class,
                data_sensitivity=data_sensitivity,
                host=host,
                output=output,
            ),
        )
        self._tool_calls.append(result)
        if result.outcome is ToolCallOutcome.DENIED:
            raise ToolCallDeniedError(result)
        if result.outcome is ToolCallOutcome.BLOCKED_INJECTION:
            raise ToolCallBlockedError(result)
        if result.outcome is ToolCallOutcome.ESCALATED:
            raise ToolCallEscalatedError(result)
        return result

    def report_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Report usage for server-side pricing and budget enforcement."""
        report = UsageReport(
            run_id=self._run.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
            cost_usd=cost_usd,
            timestamp=self._clock(),
            model_identity=self._run.model_identity,
        )
        updated = self._reporter.record_usage(self._run.id, report)
        if updated.state in _TERMINAL:
            raise RunTerminatedError(updated.state)

    def checkpoint(self) -> None:
        """Yield control: block while paused, raise when cancelled."""
        self._control.checkpoint()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_worker_context.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/worker.py tests/test_adapter_worker_context.py
git commit -m "feat(adapters): add the worker context client"
```

---

### Task 5: EntrypointLoader

**Files:**
- Create: `src/hiveplane/adapters/loader.py`
- Test: `tests/test_adapter_loader.py`

**Interfaces:**
- Consumes: `EntrypointLoadError` (Task 1).
- Produces: `EntrypointLoader(*, root: str | Path = ".")` with `load(entrypoint: str) -> Entrypoint` where `Entrypoint = Callable[[dict[str, JsonValue], WorkerContext], JsonValue]`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for entrypoint resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from hiveplane.adapters.errors import EntrypointLoadError
from hiveplane.adapters.loader import EntrypointLoader


def _write_module(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")


def test_loads_a_callable(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_ok", "def run(task, ctx):\n    return {'ok': True}\n")
    entry = EntrypointLoader(root=tmp_path).load("worker_ok:run")
    assert entry({"a": 1}, None) == {"ok": True}  # type: ignore[arg-type]


def test_unknown_module_raises(tmp_path: Path) -> None:
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader(root=tmp_path).load("worker_missing:run")


def test_unknown_attribute_raises(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_noattr", "def other(task, ctx):\n    return None\n")
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader(root=tmp_path).load("worker_noattr:run")


def test_non_callable_attribute_raises(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_const", "run = 3\n")
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader(root=tmp_path).load("worker_const:run")


def test_malformed_entrypoint_raises() -> None:
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader().load("not-an-entrypoint")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_loader.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.adapters.loader'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/adapters/loader.py`:

```python
"""Runtime entrypoint resolution (M16).

A manifest declares ``runtime.entrypoint`` as ``module:function``. The loader
imports the module with the configured root on ``sys.path`` and returns the
callable, failing fast and loudly when it cannot.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from hiveplane.adapters.errors import EntrypointLoadError
from hiveplane.adapters.worker import WorkerContext

#: A workload entrypoint: ``run(task, ctx) -> result``.
Entrypoint = Callable[[dict[str, JsonValue], WorkerContext], JsonValue]


@contextmanager
def _prepend_sys_path(root: str) -> Iterator[None]:
    sys.path.insert(0, root)
    try:
        yield
    finally:
        with suppress(ValueError):
            sys.path.remove(root)


class EntrypointLoader:
    """Resolves ``module:function`` entrypoints against a root directory."""

    def __init__(self, *, root: str | Path = ".") -> None:
        self._root = Path(root)

    def load(self, entrypoint: str) -> Entrypoint:
        """Import and return the callable named by ``entrypoint``."""
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
        if not callable(target):
            raise EntrypointLoadError(entrypoint, f"attribute {attr!r} is not callable")
        return cast("Entrypoint", target)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_loader.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/loader.py tests/test_adapter_loader.py
git commit -m "feat(adapters): add the entrypoint loader"
```

---

### Task 6: RawWorkerAdapter

**Files:**
- Create: `src/hiveplane/adapters/raw_worker.py`
- Test: `tests/test_adapter_raw_worker.py`

**Interfaces:**
- Consumes: `RunControl`/`WorkerContext`/`Entrypoint` (Tasks 3-5), `RunReporter` (Task 2), errors (Task 1), `ToolGateway`, `RuntimeAdapter`, `RunContext`, `IllegalTransitionError`.
- Produces: `RawWorkerAdapter(reporter, tools, loader, *, clock=None, spawner=None)` implementing the `Adapter` contract. Also exported helper `ThreadSpawner` type alias `Spawner`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the raw-worker adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.adapters.errors import UnsupportedAdapterError
from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(task: dict[str, object] | None = None) -> Run:
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
    def __init__(self, entry: Callable[..., object]) -> None:
        self._entry = entry

    def load(self, entrypoint: str) -> Callable[..., object]:
        return self._entry


def _adapter(
    entry: Callable[..., object], reporter: _Reporter, *, loader: _Loader | None = None
) -> RawWorkerAdapter:
    return RawWorkerAdapter(
        reporter,  # type: ignore[arg-type]
        _Tools(),  # type: ignore[arg-type]
        loader or _Loader(entry),  # type: ignore[arg-type]
        clock=lambda: _NOW,
        spawner=lambda work: work(),
    )


def _context(workload: AgentWorkload, task: dict[str, object] | None = None) -> RunContext:
    return RunContext(run=_run(task), workload=workload, sandbox=True)


def test_registers_only_raw_worker_manifests(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    adapter = _adapter(lambda task, ctx: {"ok": True}, _Reporter())
    with pytest.raises(UnsupportedAdapterError):
        adapter.register(make_manifest(adapter="langgraph"))


def test_successful_worker_completes_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    reporter = _Reporter()
    adapter = _adapter(lambda task, ctx: {"ok": True}, reporter)
    adapter.submit(_context(make_manifest()))
    assert reporter.transitions == [(RunState.COMPLETED, None)]
    assert adapter.status("run-1") is RunState.COMPLETED


def test_worker_usage_is_forwarded(make_manifest: Callable[..., AgentWorkload]) -> None:
    reporter = _Reporter()

    def entry(task: dict[str, object], ctx: object) -> dict[str, object]:
        ctx.report_usage(input_tokens=10, output_tokens=5, cost_usd=0.0)  # type: ignore[attr-defined]
        return {"ok": True}

    adapter = _adapter(entry, reporter)
    adapter.submit(_context(make_manifest()))
    assert len(reporter.usage) == 1
    assert reporter.usage[0].input_tokens == 10


def test_worker_exception_fails_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    def entry(task: dict[str, object], ctx: object) -> dict[str, object]:
        raise RuntimeError("boom")

    reporter = _Reporter()
    adapter = _adapter(entry, reporter)
    adapter.submit(_context(make_manifest()))
    assert reporter.transitions == [(RunState.FAILED, "RuntimeError: boom")]
    assert adapter.status("run-1") is RunState.FAILED


def test_tool_call_escalation_stops_without_completing(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.adapters.errors import ToolCallEscalatedError
    from hiveplane.execution.tools import ToolCallResult

    def entry(task: dict[str, object], ctx: object) -> dict[str, object]:
        raise ToolCallEscalatedError(
            ToolCallResult(run_id="run-1", tool_id="mcp.t.x", outcome=ToolCallOutcome.ESCALATED)
        )

    reporter = _Reporter()
    adapter = _adapter(entry, reporter)
    adapter.submit(_context(make_manifest()))
    assert reporter.transitions == []
    assert EventType.OPERATOR_ACTION in reporter.events


def test_pause_resume_and_cancel_delegate(make_manifest: Callable[..., AgentWorkload]) -> None:
    import threading

    started = threading.Event()
    release = threading.Event()

    def entry(task: dict[str, object], ctx: object) -> dict[str, object]:
        started.set()
        release.wait(1.0)
        ctx.checkpoint()  # type: ignore[attr-defined]
        return {"ok": True}

    adapter = RawWorkerAdapter(
        _Reporter(),  # type: ignore[arg-type]
        _Tools(),  # type: ignore[arg-type]
        _Loader(entry),  # type: ignore[arg-type]
        clock=lambda: _NOW,
    )
    adapter.submit(_context(make_manifest()))
    assert started.wait(1.0) is True
    assert adapter.pause("run-1") is True
    assert adapter.status("run-1") is RunState.RUNNING
    assert adapter.resume("run-1") is True
    release.set()
    assert adapter.cancel("run-1") is None
    assert adapter.pause("unknown") is False
    assert adapter.usage("run-1") is None
    assert adapter.tool_calls("run-1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_raw_worker.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.adapters.raw_worker'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/adapters/raw_worker.py`:

```python
"""The raw Python worker adapter: the framework-free reference runtime (M16)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.adapters.errors import (
    RunCancelledError,
    ToolCallEscalatedError,
    UnsupportedAdapterError,
    WorkerError,
)
from hiveplane.adapters.loader import Entrypoint, EntrypointLoader
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

#: Runs a unit of work; the default spawns a daemon thread.
Spawner = Callable[[Callable[[], None]], None]


def _thread_spawner(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


class RawWorkerAdapter:
    """Runs a workload entrypoint in-process and reports through the boundary."""

    def __init__(
        self,
        reporter: RunReporter,
        tools: ToolGateway,
        loader: EntrypointLoader,
        *,
        clock: Callable[[], datetime] | None = None,
        spawner: Spawner | None = None,
    ) -> None:
        self._reporter = reporter
        self._tools = tools
        self._loader = loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spawner = spawner or _thread_spawner
        self._lock = threading.Lock()
        self._entries: dict[str, Entrypoint] = {}
        self._states: dict[str, RunState] = {}
        self._controls: dict[str, RunControl] = {}
        self._tool_calls: dict[str, list[ToolCallResult]] = {}
        self._usage: dict[str, UsageReport | None] = {}

    def register(self, workload: AgentWorkload) -> None:
        """Load and cache a workload's entrypoint, rejecting other adapters."""
        if workload.spec.runtime.adapter is not RuntimeAdapter.RAW_WORKER:
            raise UnsupportedAdapterError(workload.spec.runtime.adapter.value)
        self._entries[workload.name] = self._loader.load(workload.spec.runtime.entrypoint)

    def submit(self, context: RunContext) -> None:
        """Start the workload on the configured spawner and return immediately."""
        entry = self._entry(context.workload)
        control = RunControl()
        with self._lock:
            self._states[context.run.id] = RunState.RUNNING
            self._controls[context.run.id] = control
            self._tool_calls[context.run.id] = []
            self._usage[context.run.id] = None
        self._spawner(lambda: self._execute(entry, context, control))

    def pause(self, run_id: str) -> bool:
        """Request a cooperative pause."""
        control = self._controls.get(run_id)
        if control is None:
            return False
        control.pause()
        return True

    def resume(self, run_id: str) -> bool:
        """Clear a pause request."""
        control = self._controls.get(run_id)
        if control is None:
            return False
        control.resume()
        return True

    def cancel(self, run_id: str) -> None:
        """Request cancellation; the run state is owned by the control plane."""
        control = self._controls.get(run_id)
        if control is not None:
            control.cancel()

    def status(self, run_id: str) -> RunState:
        """Return the adapter's last recorded state."""
        return self._states.get(run_id, RunState.QUEUED)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return the last usage report seen for the run."""
        return self._usage.get(run_id)

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        """Return the tool calls routed through the boundary for the run."""
        return list(self._tool_calls.get(run_id, []))

    def _entry(self, workload: AgentWorkload) -> Entrypoint:
        entry = self._entries.get(workload.name)
        if entry is None:
            self.register(workload)
            entry = self._entries[workload.name]
        return entry

    def _execute(self, entry: Entrypoint, context: RunContext, control: RunControl) -> None:
        run = context.run
        tool_calls = self._tool_calls[run.id]
        ctx = WorkerContext(
            run=run,
            workload=context.workload,
            sandbox=context.sandbox,
            tools=self._tools,
            reporter=self._reporter,
            control=control,
            tool_calls=tool_calls,
            clock=self._clock,
        )
        try:
            result = entry(ctx.task, ctx)
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
        except Exception as exc:  # noqa: BLE001 - worker code is untrusted
            self._fail(run.id, f"{type(exc).__name__}: {exc}")
            return
        self._reporter.transition(run.id, RunState.COMPLETED, actor="adapter", result=result)
        with self._lock:
            self._states[run.id] = RunState.COMPLETED

    def _fail(self, run_id: str, reason: str) -> None:
        try:
            self._reporter.transition(
                run_id,
                RunState.FAILED,
                actor="adapter",
                detail=reason,
                failure_reason=reason,
            )
        except IllegalTransitionError:
            pass
        with self._lock:
            self._states[run_id] = RunState.FAILED
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_raw_worker.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/adapters/raw_worker.py tests/test_adapter_raw_worker.py
git commit -m "feat(adapters): add the raw worker adapter"
```

---

### Task 7: RunService reporting seam

**Files:**
- Modify: `src/hiveplane/execution/service.py`
- Modify: `src/hiveplane/adapters/__init__.py`
- Test: `tests/test_adapter_service_reporter.py`

**Interfaces:**
- Consumes: `RunReporter` (Task 2), `AdapterRunExecutor`/`Adapter` (`adapters/base.py`), `NullRunExecutor` (`execution/gates.py`).
- Produces:
  - `RunService(..., executor: RunExecutor | None = None)`; defaults to `NullRunExecutor`.
  - `RunService.attach_executor(executor: RunExecutor) -> None`.
  - `RunService.transition(..., result: JsonValue | None = None)` persists the result.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for RunService as a reporting seam."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import JsonValue

from hiveplane.adapters.base import AdapterRunExecutor
from hiveplane.adapters.reporter import RunReporter
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord, RunContext
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


class _FanOut:
    def notify(self, run: object, workload: object) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: object, workload: object) -> list[DeliveryRecord]:
        return []


class _CapturingExecutor:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, context: RunContext) -> None:
        self.started.append(context.run.id)

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


def _service(make_manifest: Callable[..., AgentWorkload]) -> RunService:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(name="agent-1")
    registry.create(workload)
    return RunService(
        InMemoryRunStore(),
        registry,
        admission=AdmissionPipeline(
            _Cert(True),
            _Policy(DecisionOutcome.ALLOW),
            _Budget(True),
            _Sandbox(False),
        ),
        executor=None,
        fanout=_FanOut(),  # type: ignore[arg-type]
    )


def test_run_service_satisfies_run_reporter(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    assert isinstance(_service(make_manifest), RunReporter)


def test_attach_executor_uses_the_new_executor(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service = _service(make_manifest)
    executor = _CapturingExecutor()
    service.attach_executor(executor)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX
    )
    service.start(run.id, actor="cli")
    assert executor.started == [run.id]


def test_transition_persists_a_result(make_manifest: Callable[..., AgentWorkload]) -> None:
    service = _service(make_manifest)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    service.start(run.id, actor="cli")
    updated = service.transition(
        run.id, RunState.COMPLETED, actor="adapter", result={"ok": True}
    )
    assert updated.result == {"ok": True}
    assert service.get(run.id).result == {"ok": True}


def test_adapter_run_executor_start_delegates(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    class _Adapter:
        def __init__(self) -> None:
            self.submitted: list[RunContext] = []

        def register(self, workload: object) -> None:
            return None

        def submit(self, context: RunContext) -> None:
            self.submitted.append(context)

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

        def tool_calls(self, run_id: str) -> list[JsonValue]:
            return []

    adapter = _Adapter()
    executor = AdapterRunExecutor(adapter)  # type: ignore[arg-type]
    service = _service(make_manifest)
    service.attach_executor(executor)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    service.start(run.id, actor="cli")
    assert [ctx.run.id for ctx in adapter.submitted] == [run.id]
```

Note: `test_execution_admission._Cert` takes `(admitted, model=None)`; `_Policy` requires an explicit `DecisionOutcome`; `_Budget(allowed)`; `_Sandbox(required)`. This test uses `_Policy(DecisionOutcome.ALLOW)` and `_Budget(True)` deliberately — it exercises wiring, not pricing.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_service_reporter.py -q`
Expected: FAIL with `TypeError: RunService.__init__() got an unexpected keyword argument ...` / `AttributeError: 'RunService' object has no attribute 'attach_executor'`

- [ ] **Step 3: Write minimal implementation**

In `src/hiveplane/execution/service.py`:

1. Merge `NullRunExecutor` into the existing `execution.gates` import block (`JsonValue` is already imported from `pydantic`):

2. Make the executor optional and store it:

```python
        executor: RunExecutor | None = None,
```

and in the body:

```python
        self._executor: RunExecutor = executor or NullRunExecutor()
```

3. Add the attach method after `__init__`:

```python
    def attach_executor(self, executor: RunExecutor) -> None:
        """Bind the runtime adapter that executes runs after service construction."""
        self._executor = executor
```

4. Extend `transition` with a `result` parameter and persist it:

```python
        result: JsonValue | None = None,
```

```python
        if result is not None:
            updates["result"] = result
```

Also update the `RunReporter` protocol call shape: `RunService.transition` already matches after adding `result`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_service_reporter.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite to catch regressions**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all existing run-service tests still green; `executor` is now optional)

- [ ] **Step 6: Commit**

```bash
git add src/hiveplane/execution/service.py tests/test_adapter_service_reporter.py
git commit -m "feat(execution): add adapter reporting seam to the run service"
```

---

### Task 8: Wiring and configuration

**Files:**
- Modify: `src/hiveplane/config.py`
- Modify: `src/hiveplane/execution/wiring.py`
- Modify: `src/hiveplane/api/app.py`
- Test: `tests/test_adapter_wiring.py`

**Interfaces:**
- Consumes: `RawWorkerAdapter` (Task 6), `EntrypointLoader` (Task 5), `AdapterRunExecutor`, `ToolGateway`, `ShapingPipeline`, `InjectionScanner`, `get_settings`.
- Produces:
  - `ExecutionSettings.entrypoints_root: str = "."`
  - `execution/wiring.py:build_tool_gateway(registry, policy, run_service, approvals) -> ToolGateway`
  - `execution/wiring.py:attach_raw_worker(run_service, tool_gateway, *, root=None, spawner=None) -> RawWorkerAdapter`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for adapter wiring."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.config import Settings
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.wiring import attach_raw_worker, build_tool_gateway
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Cert, _Sandbox


def test_entrypoints_root_default() -> None:
    assert Settings().execution.entrypoints_root == "."


class _FanOut:
    def notify(self, run: object, workload: object) -> list[object]:
        return []

    def notify_escalation(self, run: object, workload: object) -> list[object]:
        return []


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
        fanout=_FanOut(),  # type: ignore[arg-type]
        budget=budget,
    )
    return service, registry, policy


def test_attach_raw_worker_completes_a_run(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    (tmp_path / "wiring_worker.py").write_text(
        "def run(task, ctx):\n"
        "    ctx.report_usage(input_tokens=100, output_tokens=50, cost_usd=0.0)\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    workload = make_manifest(
        name="agent-1",
        runtime={"adapter": "raw-worker", "entrypoint": "wiring_worker:run"},
    )
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = attach_raw_worker(
        service, gateway, root=tmp_path, spawner=lambda work: work()
    )
    assert isinstance(adapter, RawWorkerAdapter)

    run = service.submit(
        workload="agent-1",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity="openai/gpt-4o/2024-08-06",
    )
    service.start(run.id, actor="cli")
    completed = service.get(run.id)
    assert completed.state is RunState.COMPLETED
    assert completed.result == {"ok": True}
    assert completed.cost_usd > 0.0
```

Note: `_Cert(admitted, model=None)` and `_Sandbox(required)` are the shared test doubles from `tests/test_execution_admission.py`. `BudgetService` prices usage server-side using the run's `model_identity`, so `cost_usd > 0` proves the `report_usage` path is live. The synchronous `spawner` makes the run deterministic.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_wiring.py -q`
Expected: FAIL with `ImportError: cannot import name 'attach_raw_worker' from 'hiveplane.execution.wiring'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/config.py` — add to `ExecutionSettings`:

```python
    entrypoints_root: str = "."
```

`src/hiveplane/execution/wiring.py` — add imports and functions. Do **not** import `api` from `execution`; import the shaping pieces directly:

```python
from hiveplane.adapters.base import AdapterRunExecutor
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.adapters.raw_worker import RawWorkerAdapter, Spawner
from hiveplane.execution.tools import ToolGateway
from hiveplane.shaping.injection import InjectionScanner
from hiveplane.shaping.pipeline import ShapingPipeline
```

```python
def build_tool_gateway(
    registry_service: RegistryService,
    policy_gate: PolicyGate,
    run_service: RunService,
    approvals: ApprovalRequests | None,
) -> ToolGateway:
    """Build the tool-call boundary over the live run service."""
    return ToolGateway(
        registry_service,
        policy_gate,
        run_service,
        shaping=ShapingPipeline(InjectionScanner()),
        approvals=approvals,
    )


def attach_raw_worker(
    run_service: RunService,
    tool_gateway: ToolGateway,
    *,
    root: str | None = None,
    spawner: Spawner | None = None,
) -> RawWorkerAdapter:
    """Build the raw-worker adapter and bind it as the run service's executor."""
    settings = get_settings()
    adapter = RawWorkerAdapter(
        run_service,
        tool_gateway,
        EntrypointLoader(root=root or settings.execution.entrypoints_root),
        spawner=spawner,
    )
    run_service.attach_executor(AdapterRunExecutor(adapter))
    return adapter
```

`build_run_service` keeps its signature; it already builds `RunService(executor=NullRunExecutor())`. Leave it, and let `app.py` override with `attach_raw_worker`.

`src/hiveplane/api/app.py`:
- Replace the inline `ToolGateway(...)` construction with `build_tool_gateway(...)`.
- After `app.state.run_service` is set, call `attach_raw_worker(app.state.run_service, app.state.tool_gateway)` and store `app.state.adapter`.
- Update imports: remove `ToolGateway`, `ShapingPipeline`, `InjectionScanner` if now unused; add `attach_raw_worker`, `build_tool_gateway`, `RawWorkerAdapter`.

```python
    app.state.run_service = run_service or build_run_service(
        registry, policy_engine, approval_service, budget_service, sandbox_manager
    )
    app.state.tool_gateway = build_tool_gateway(
        registry, policy_engine, app.state.run_service, approval_service
    )
    app.state.adapter = attach_raw_worker(app.state.run_service, app.state.tool_gateway)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adapter_wiring.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite to catch regressions**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (the existing API/service/wiring tests still pass)

- [ ] **Step 6: Commit**

```bash
git add src/hiveplane/config.py src/hiveplane/execution/wiring.py src/hiveplane/api/app.py tests/test_adapter_wiring.py
git commit -m "feat(adapters): wire the raw worker into the control plane"
```

---

### Task 9: Reference worker, exports, and docs

**Files:**
- Create: `examples/repo_agent.py`
- Modify: `src/hiveplane/adapters/__init__.py`
- Modify: `docs/ADAPTERS.md`
- Modify: `docs/wbs/v0.1.0/wbs-v0.1.0-part8-adapters.md`
- Modify: `docs/wbs/v0.1.0/wbs-v0.1.0-index.md`
- Test: `tests/test_adapter_reference_worker.py`

**Interfaces:**
- Consumes: `EntrypointLoader`, `WorkerContext`.
- Produces: `examples.repo_agent.run(task, ctx) -> dict[str, JsonValue]`; expanded `hiveplane.adapters` exports.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the bundled reference worker entrypoint."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.adapters.worker import RunControl, WorkerContext
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_bundled_repo_agent_entrypoint_loads() -> None:
    entry = EntrypointLoader(root=_PROJECT_ROOT).load("examples.repo_agent:run")
    assert callable(entry)


def test_bundled_repo_agent_runs(make_manifest: Callable[..., AgentWorkload]) -> None:
    entry = EntrypointLoader(root=_PROJECT_ROOT).load("examples.repo_agent:run")
    calls: list[ToolCallResult] = []
    usage: list[object] = []
    run = Run(
        id="run-1",
        workload_id="repo-agent",
        caller="cli",
        state=RunState.RUNNING,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.SANDBOX,
        task={"repo": "hiveplane"},
    )

    class _Tools:
        def invoke(self, run_id: str, request: object) -> ToolCallResult:
            return ToolCallResult(
                run_id=run_id, tool_id="mcp.github.list_pull_requests", outcome=ToolCallOutcome.ALLOWED
            )

    class _Reporter:
        def get(self, run_id: str) -> Run:
            return run

        def transition(self, run_id: str, target: RunState, **kwargs: object) -> Run:
            return run

        def record_usage(self, run_id: str, report: object) -> Run:
            usage.append(report)
            return run

        def record_event(self, run_id: str, event_type: object, actor: str, **kwargs: object) -> None:
            return None

    ctx = WorkerContext(
        run=run,
        workload=make_manifest(name="repo-agent"),
        sandbox=True,
        tools=_Tools(),  # type: ignore[arg-type]
        reporter=_Reporter(),  # type: ignore[arg-type]
        control=RunControl(),
        tool_calls=calls,
        clock=lambda: _NOW,
    )
    result = entry({"repo": "hiveplane"}, ctx)
    assert isinstance(result, dict)
    assert len(calls) == 1
    assert len(usage) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adapter_reference_worker.py -q`
Expected: FAIL with `EntrypointLoadError: cannot load entrypoint 'examples.repo_agent:run'`

- [ ] **Step 3: Write the reference worker**

`examples/repo_agent.py`:

```python
"""Reference raw-worker workload: summarize open pull requests.

Demonstrates the worker contract: route tool calls through the control-plane
boundary, report usage, and return a result.
"""

from __future__ import annotations

from typing import Any

from hiveplane.adapters.worker import WorkerContext


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    """List open pull requests and report a short summary."""
    pulls = ctx.tool_call(
        "mcp.github.list_pull_requests",
        host="api.github.com",
        output="[]",
    )
    ctx.report_usage(input_tokens=120, output_tokens=40, tool_calls=1, cost_usd=0.0)
    return {"repo": task.get("repo"), "tool": pulls.tool_id, "status": "summarized"}
```

- [ ] **Step 4: Update exports**

`src/hiveplane/adapters/__init__.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_adapter_reference_worker.py -q`
Expected: PASS (2 tests)

- [ ] **Step 6: Update docs**

In `docs/ADAPTERS.md`, under **Typed contract**, add a **"Writing a worker"** subsection:

```markdown
### Writing a worker

A raw-worker entrypoint is `module:function` (from `spec.runtime.entrypoint`) and has the
signature `run(task: dict, ctx: WorkerContext) -> JsonValue`.

```python
from hiveplane.adapters.worker import WorkerContext

def run(task: dict, ctx: WorkerContext) -> dict:
    result = ctx.tool_call("mcp.github.list_pull_requests", host="api.github.com")
    ctx.report_usage(input_tokens=120, output_tokens=40, tool_calls=1)
    ctx.checkpoint()
    return {"tool": result.tool_id}
```

- `ctx.tool_call(...)` routes through the policy, egress, and shaping boundary and raises
  `ToolCallDeniedError`, `ToolCallBlockedError`, or `ToolCallEscalatedError` when the call is not allowed.
- `ctx.report_usage(...)` is priced server-side and enforced against the run budget;
  it raises `RunTerminatedError` if the run has gone terminal.
- `ctx.checkpoint()` blocks while the run is paused and raises `RunCancelledError` when stopped.
- The adapter records the returned value as the run result and reports the terminal state;
  it never decides policy.
```

In `docs/wbs/v0.1.0/wbs-v0.1.0-part8-adapters.md`, check the completed boxes for #42 and #43:

```markdown
- [x] [#42](https://github.com/deghosal-2026/hiveplane/issues/42) — Adapter interface definition
- [x] [#43](https://github.com/deghosal-2026/hiveplane/issues/43) — Raw Python worker reference adapter
```

In `docs/wbs/v0.1.0/wbs-v0.1.0-index.md`, update the progress line to reflect the new count (Parts 1–7 complete plus M16's #42–#43):

```markdown
> **Progress:** Parts 1-7 complete, plus M16 (#42-#43) — 42/60 issues closed. Parts 8 (M17), 9-13 remain.
```

- [ ] **Step 7: Run the full exit gate**

Run: `make check`
Expected: ruff clean, mypy strict clean, `pytest` all pass, coverage total > 95%

- [ ] **Step 8: Commit**

```bash
git add examples/repo_agent.py src/hiveplane/adapters/__init__.py tests/test_adapter_reference_worker.py docs/ADAPTERS.md docs/wbs/v0.1.0/wbs-v0.1.0-part8-adapters.md docs/wbs/v0.1.0/wbs-v0.1.0-index.md
git commit -m "feat(adapters): ship the raw worker adapter and reference workload"
```

---

## As-Built Deviations

Applied while executing this plan; the committed code is the source of truth.

- Exception names carry the `Error` suffix (`ToolCallDeniedError`, `ToolCallBlockedError`,
  `ToolCallEscalatedError`, `RunTerminatedError`, `RunCancelledError`) to satisfy ruff `N818`.
- `contextlib.suppress` replaces `try/except/pass` (ruff `SIM105`) in `loader.py` and
  `raw_worker.py`.
- `EntrypointLoader.load` casts the resolved attribute to `Entrypoint` (mypy `no-any-return`).
- `RawWorkerAdapter._execute` catches broad `Exception` without a `noqa` (the rule is not enabled).
- Worker/control tests read properties into locals before asserting, and use `JsonValue` task
  types on the `WorkerContext`/`Entrypoint` boundary, to stay mypy-strict clean.
- **The raw worker is opt-in in the app**: `ExecutionSettings.adapter: Literal["none", "raw-worker"]`
  defaults to `"none"`, and `create_app` attaches the adapter only when it is `"raw-worker"`
  (`HIVEPLANE_EXECUTION__ADAPTER=raw-worker`). This mirrors the certification executor's
  opt-in (#63) and keeps `create_app()` deterministic for existing API tests; `attach_raw_worker`
  is still the direct wiring entry point used by the end-to-end test and by deployments.
- `attach_raw_worker(root=...)` accepts `str | Path | None` so tests can pass `tmp_path`.

## Self-Review

**Spec coverage (design sections):**
- Components (`errors`, `reporter`, `worker`, `loader`, `raw_worker`) → Tasks 1–6.
- Worker contract (`tool_call`, `report_usage`, `checkpoint`, metadata) → Task 4.
- Adapter lifecycle (register/submit/pause/resume/cancel/status/usage/tool_calls, background spawner, terminal reporting, escalation) → Task 6.
- Wiring (`attach_executor`, `build_tool_gateway`, `attach_raw_worker`, config root) → Tasks 7–8.
- Errors → Task 1.
- Reference worker + docs → Task 9.
- Testing across all units + end-to-end → each task, with an end-to-end wiring test in Task 8.

**Type consistency:** `RunReporter.transition` includes `result`; `RunService.transition` gains `result` in Task 7 before the adapter (Task 6) calls it. `Entrypoint` returns `JsonValue`; `RawWorkerAdapter` passes `entry(ctx.task, ctx)`. `WorkerContext` constructor keyword names are identical in Tasks 4, 6, and 9. `Spawner` is defined in `raw_worker.py` and imported by `wiring.py`.

**Known forward work (out of scope, Part 12/field test):** replay after an escalated run is approved; LangGraph adapter (#44); conformance suite (#45).