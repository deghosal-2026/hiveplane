"""Tests for the subprocess worker that runs agents in a sandboxed child (M23, #110b).

The worker re-imports the workload entrypoint and drives it with an
HTTP-backed WorkerContext so tool and model calls route back to the
control-plane boundary. Tests inject a fake transport instead of a live server;
the live-server integration is covered in the caps/wiring slices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hiveplane.adapters.errors import ToolCallDeniedError
from hiveplane.execution.subprocess_worker import (
    HttpWorkerContext,
    SandboxWorkerSpec,
    run_worker,
)
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult
from hiveplane.llm.models import CompletionResult
from hiveplane.shaping.pipeline import ShapedOutput

_RUN = "run-1"
_BASE = "http://127.0.0.1:9/"


def _tool_result(outcome: ToolCallOutcome) -> dict:
    return ToolCallResult(
        run_id=_RUN,
        tool_id="mcp.t.read",
        outcome=outcome,
        rule="test",
        reason=None,
        shaped_output=ShapedOutput(
            text="{}", original_bytes=2, shaped_bytes=2, truncated=False
        ),
        approval_id=None,
        egress_checked=False,
    ).model_dump()


def _model_result() -> dict:
    return CompletionResult(
        content="hi",
        model_identity="openai/gpt-4o/2024-08-06",
        usage={"input_tokens": 3, "output_tokens": 2},
        finish_reason="stop",
    ).model_dump()


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.tool_result: dict = _tool_result(ToolCallOutcome.ALLOWED)
        self.model_result: dict = _model_result()

    def __call__(
        self, method: str, url: str, payload: dict | None
    ) -> tuple[int, dict]:
        self.calls.append((method, url, payload))
        if url.endswith("/tool-call"):
            return 200, self.tool_result
        if url.endswith("/model"):
            return 200, self.model_result
        if url.endswith("/control"):
            return 200, {"state": "running", "paused": False, "cancelled": False}
        return 200, {}


def _ctx(transport: _FakeTransport) -> HttpWorkerContext:
    return HttpWorkerContext(
        base_url=_BASE, run_id=_RUN, token="tok", transport=transport
    )


def test_tool_call_returns_the_governed_result() -> None:
    transport = _FakeTransport()
    ctx = _ctx(transport)

    result = ctx.tool_call("mcp.t.read", host="api.example.com")

    assert result.outcome is ToolCallOutcome.ALLOWED
    assert result.shaped_output is not None and result.shaped_output.text == "{}"
    method, url, payload = transport.calls[0]
    assert method == "POST"
    assert url.endswith(f"/internal/sandbox/{_RUN}/tool-call")
    assert payload is not None and payload["tool_id"] == "mcp.t.read"


def test_tool_call_denied_raises() -> None:
    transport = _FakeTransport()
    transport.tool_result = _tool_result(ToolCallOutcome.DENIED)
    ctx = _ctx(transport)

    with pytest.raises(ToolCallDeniedError):
        ctx.tool_call("mcp.t.read")


def test_complete_invokes_the_model_seam() -> None:
    transport = _FakeTransport()
    ctx = _ctx(transport)

    result = ctx.complete("hello")

    assert result.content == "hi"
    assert result.usage.total_tokens == 5
    _, url, payload = transport.calls[0]
    assert url.endswith(f"/internal/sandbox/{_RUN}/model")
    assert payload is not None and payload["messages"] == [
        {"role": "user", "content": "hello"}
    ]


def _entrypoint_module(tmp_path: Path, body: str, name: str = "agent") -> str:
    (tmp_path / f"{name}.py").write_text(
        f"def run(task, ctx):\n{body}\n", encoding="utf-8"
    )
    return f"{name}:run"


def test_run_worker_posts_result_on_success(tmp_path: Path) -> None:
    transport = _FakeTransport()
    entrypoint = _entrypoint_module(tmp_path, "    return {'risk': 'low'}", name="agent_ok")
    spec = SandboxWorkerSpec(
        run_id=_RUN,
        entrypoint=entrypoint,
        task={"pr": {"title": "x"}},
        base_url=_BASE,
        token="tok",
        root=str(tmp_path),
    )

    code = run_worker(spec, transport=transport)

    assert code == 0
    result_calls = [c for c in transport.calls if c[1].endswith("/result")]
    assert result_calls and result_calls[0][2] == {"result": {"risk": "low"}}


def test_run_worker_reports_failure_on_exception(tmp_path: Path) -> None:
    transport = _FakeTransport()
    entrypoint = _entrypoint_module(
        tmp_path, "    raise RuntimeError('boom')", name="agent_boom"
    )
    spec = SandboxWorkerSpec(
        run_id=_RUN,
        entrypoint=entrypoint,
        task={},
        base_url=_BASE,
        token="tok",
        root=str(tmp_path),
    )

    code = run_worker(spec, transport=transport)

    assert code == 1
    failure_calls = [c for c in transport.calls if c[1].endswith("/failure")]
    assert failure_calls and "boom" in str(failure_calls[0][2])


def test_checkpoint_stops_when_cancelled(tmp_path: Path) -> None:
    from hiveplane.adapters.errors import RunCancelledError

    transport = _FakeTransport()

    def cancelled_control(
        method: str, url: str, payload: dict | None
    ) -> tuple[int, dict]:
        if url.endswith("/control"):
            return 200, {"state": "cancelled", "paused": False, "cancelled": True}
        return transport(method, url, payload)

    ctx = HttpWorkerContext(
        base_url=_BASE, run_id=_RUN, token="tok", transport=cancelled_control
    )

    with pytest.raises(RunCancelledError):
        ctx.checkpoint()
