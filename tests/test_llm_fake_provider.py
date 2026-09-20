"""Tests for the deterministic fake LLM provider (M23, #107, #137)."""

from __future__ import annotations

import pytest

from hiveplane.llm.fake import FakeProvider, ReplayMissingError, replay_key
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


def test_replay_key_covers_the_full_request() -> None:
    system = Message(role="system", content="Alert: database unavailable")
    user = Message(role="user", content="Triage the alert.")
    base = CompletionRequest(messages=[system, user], model="openai/gpt-4o/2024-08-06")

    same = CompletionRequest(messages=[system, user], model="openai/gpt-4o/2024-08-06")
    other_task = CompletionRequest(
        messages=[Message(role="system", content="Alert: disk full"), user],
        model="openai/gpt-4o/2024-08-06",
    )
    other_model = CompletionRequest(messages=[system, user], model="openai/gpt-3.5/1")

    assert replay_key(base) == replay_key(same)
    assert replay_key(base) != replay_key(other_task)
    assert replay_key(base) != replay_key(other_model)


def test_replay_map_overrides_default_behavior() -> None:
    request = _request("classify this pull request")
    provider = FakeProvider(replay={replay_key(request): "risk: low"})

    response = provider.complete(request)

    assert response.content == "risk: low"


def test_replay_differs_per_task() -> None:
    low = _request("Alert: docs typo")
    high = _request("Alert: auth change")
    provider = FakeProvider(
        replay={replay_key(low): "risk: low", replay_key(high): "risk: high"}
    )

    assert provider.complete(low).content == "risk: low"
    assert provider.complete(high).content == "risk: high"


def test_missing_replay_entry_fails_fast_when_replay_is_configured() -> None:
    request = _request("unknown prompt")
    provider = FakeProvider(replay={replay_key(_request("known")): "risk: low"})

    with pytest.raises(ReplayMissingError):
        provider.complete(request)


def test_echo_fallback_without_configured_replay() -> None:
    response = FakeProvider().complete(_request("hello"))

    assert response.content == "fake:hello"
