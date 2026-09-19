"""Tests for the deterministic fake LLM provider (M23, #107)."""

from __future__ import annotations

from hiveplane.llm.fake import FakeProvider
from hiveplane.llm.models import CompletionRequest, Message


def _request(
    content: str = "classify this pull request", model: str = "fake/echo/1"
) -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content=content)], model=model)


def test_same_request_yields_same_response() -> None:
    provider = FakeProvider()
    request = _request()

    first = provider.complete(request)
    second = provider.complete(request)

    assert first.content == second.content
    assert first.model_identity == "fake/echo/1"


def test_response_reports_usage_and_finish_reason() -> None:
    response = FakeProvider().complete(_request("hello world"))

    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0
    assert response.usage.total_tokens == (
        response.usage.input_tokens + response.usage.output_tokens
    )
    assert response.finish_reason == "stop"


def test_replay_map_overrides_default_behavior() -> None:
    provider = FakeProvider(replay={"classify this pull request": "risk: low"})

    response = provider.complete(_request("classify this pull request"))

    assert response.content == "risk: low"
