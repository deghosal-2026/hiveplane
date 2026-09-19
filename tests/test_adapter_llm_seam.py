"""Tests for the LLM invocation seam on WorkerContext (M23, #115)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.adapters.errors import ModelIdentityMismatchError
from hiveplane.adapters.worker import RunControl, WorkerContext
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult
from hiveplane.llm.fake import FakeProvider
from hiveplane.llm.models import CompletionRequest, CompletionResponse, TokenUsage

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _MismatchProvider:
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            content="ok",
            model_identity="openai/gpt-3.5/2024-01-01",
            usage=TokenUsage(input_tokens=3, output_tokens=2),
            finish_reason="stop",
        )


def _run(model_identity: str | None = "openai/gpt-4o/2024-08-06") -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.RUNNING,
        model_identity=model_identity,
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.SANDBOX,
        task={"repo": "hiveplane"},
    )


class _Tools:
    def invoke(self, run_id: str, request: object) -> ToolCallResult:
        return ToolCallResult(run_id=run_id, tool_id="mcp.t.x", outcome=ToolCallOutcome.ALLOWED)


class _Reporter:
    def __init__(self, state: RunState = RunState.RUNNING) -> None:
        self._state = state
        self.usage: list[UsageReport] = []
        self.events: list[tuple[object, str | None]] = []

    def get(self, run_id: str) -> Run:
        return _run()

    def transition(self, run_id: str, target: RunState, **kwargs: object) -> Run:
        return _run()

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        self.usage.append(report)
        return _run(self._state)

    def record_event(
        self, run_id: str, event_type: object, actor: str, *, detail: str | None = None
    ) -> None:
        self.events.append((event_type, detail))


def _context(
    make_manifest: Callable[..., AgentWorkload],
    *,
    provider: object,
    reporter: _Reporter,
    run: Run | None = None,
) -> WorkerContext:
    return WorkerContext(
        run=run or _run(),
        workload=make_manifest(),
        sandbox=True,
        tools=_Tools(),  # type: ignore[arg-type]
        reporter=reporter,
        control=RunControl(),
        tool_calls=[],
        clock=lambda: _NOW,
        provider=provider,  # type: ignore[arg-type]
    )


def test_complete_returns_content_and_reports_usage(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    reporter = _Reporter()
    ctx = _context(make_manifest, provider=FakeProvider(), reporter=reporter)

    result = ctx.complete("summarize this")

    assert result.content == "fake:summarize this"
    assert result.model_identity == "openai/gpt-4o/2024-08-06"
    assert len(reporter.usage) == 1
    assert reporter.usage[0].input_tokens > 0
    assert reporter.usage[0].output_tokens > 0
    assert reporter.usage[0].model_identity == "openai/gpt-4o/2024-08-06"


def test_complete_emits_model_call_span(
    make_manifest: Callable[..., AgentWorkload], telemetry_spans: object
) -> None:
    ctx = _context(make_manifest, provider=FakeProvider(), reporter=_Reporter())

    ctx.complete("summarize this")

    spans = [span for span in telemetry_spans.spans if span.name == "model_call"]  # type: ignore[attr-defined]
    assert spans
    attributes = spans[0].attributes
    assert attributes["model_identity"] == "openai/gpt-4o/2024-08-06"
    assert attributes["input_tokens"] > 0
    assert attributes["output_tokens"] > 0
    assert attributes["cost_usd"] == 0.0
    assert attributes["finish_reason"] == "stop"


def test_model_identity_mismatch_raises_and_records_security_event(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    reporter = _Reporter()
    ctx = _context(make_manifest, provider=_MismatchProvider(), reporter=reporter)

    with pytest.raises(ModelIdentityMismatchError):
        ctx.complete("summarize this")

    assert reporter.usage == []
    assert any(
        detail is not None and "security" in detail for _, detail in reporter.events
    )


def test_complete_falls_back_to_manifest_identity(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    reporter = _Reporter()
    ctx = _context(
        make_manifest, provider=FakeProvider(), reporter=reporter, run=_run(model_identity=None)
    )

    result = ctx.complete("summarize this")

    assert result.model_identity == "openai/gpt-4o/2024-08-06"
