"""Tests for the real LLM-backed example agents (M23, #108)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import examples.repo_agent as repo_agent
import pytest

from hiveplane.llm.fake import FakeProvider
from hiveplane.llm.models import (
    CompletionRequest,
    CompletionResult,
    Message,
)


class _FakeCtx:
    """A minimal WorkerContext stand-in that routes complete() to a provider."""

    def __init__(self, provider: FakeProvider, *, tool_text: str = "{}") -> None:
        self._provider = provider
        self._tool_text = tool_text
        self.model_identity = "openai/gpt-4o/2024-08-06"
        self.tool_ids: list[tuple[str, dict[str, Any]]] = []
        self.checkpoints = 0
        self.usage: list[dict[str, Any]] = []

    def tool_call(self, tool_id: str, **kwargs: Any) -> Any:
        self.tool_ids.append((tool_id, kwargs))
        return SimpleNamespace(
            tool_id=tool_id,
            outcome="allowed",
            shaped_output=SimpleNamespace(text=self._tool_text),
        )

    def complete(self, prompt: str | list[Message], **kwargs: Any) -> CompletionResult:
        messages = (
            [Message(role="user", content=prompt)] if isinstance(prompt, str) else list(prompt)
        )
        response = self._provider.complete(
            CompletionRequest(messages=messages, model=self.model_identity)
        )
        return CompletionResult(
            content=response.content,
            model_identity=response.model_identity,
            usage=response.usage,
            finish_reason=response.finish_reason,
        )

    def checkpoint(self) -> None:
        self.checkpoints += 1

    def report_usage(self, **kwargs: Any) -> None:
        self.usage.append(dict(kwargs))


def test_repo_agent_returns_structured_risk() -> None:
    provider = FakeProvider(
        replay={
            repo_agent.CLASSIFY_PROMPT: json.dumps(
                {"risk": "low", "summary": "docs-only change"}
            )
        }
    )
    ctx = _FakeCtx(provider, tool_text=json.dumps({"pull_requests": [{"number": 412}]}))

    result = repo_agent.run({"pr": {"title": "Fix typo in README"}}, ctx)  # type: ignore[arg-type]

    assert result == {"risk": "low", "summary": "docs-only change"}
    assert ctx.tool_ids[0][0] == "mcp.github.list_pull_requests"
    assert "output" not in ctx.tool_ids[0][1]


def test_repo_agent_tolerates_non_json_completion() -> None:
    provider = FakeProvider(replay={repo_agent.CLASSIFY_PROMPT: "medium"})
    ctx = _FakeCtx(provider, tool_text="{}")

    result = repo_agent.run({}, ctx)  # type: ignore[arg-type]

    assert result["risk"] == "medium"


def test_docs_agent_drafts_with_model() -> None:
    pytest.importorskip("langgraph")
    import examples.docs_agent as docs_agent
    from langgraph.types import Command

    provider = FakeProvider(
        replay={docs_agent.DRAFT_PROMPT: json.dumps({"draft": "new docs section"})}
    )
    ctx = _FakeCtx(provider, tool_text="{}")
    config: Any = {"configurable": {"thread_id": "docs-run-1", "hiveplane_ctx": ctx}}
    graph: Any = docs_agent.graph

    chunks = list(graph.stream({"task": {"issue": "README"}}, config, stream_mode="values"))

    assert any("__interrupt__" in chunk for chunk in chunks)
    assert ctx.checkpoints >= 1
    assert ctx.tool_ids[0][0] == "mcp.github.read_issue"

    list(graph.stream(Command(resume=True), config, stream_mode="values"))
    values = graph.get_state(config).values

    assert values["result"]["summary"] == "new docs section"


def test_incident_agent_triages_alert() -> None:
    import examples.incident_agent as incident_agent

    provider = FakeProvider(
        replay={
            incident_agent.TRIAGE_PROMPT: json.dumps(
                {"severity": "critical", "summary": "database unavailable"}
            )
        }
    )
    ctx = _FakeCtx(provider, tool_text=json.dumps({"result": []}))

    result = incident_agent.run({"alert": {"service": "db"}}, ctx)  # type: ignore[arg-type]

    assert result == {"severity": "critical", "summary": "database unavailable"}
    assert [tool_id for tool_id, _ in ctx.tool_ids] == [
        "prometheus.query",
        "pagerduty.acknowledge",
    ]
