"""L7 — the local LLM must actually serve inference (M23, #93/#94).

These tests are intentionally strict: the docker suite runs against real local
inference, so an unreachable or non-serving endpoint is a FAILURE, not a skip.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.docker
def test_local_llm_returns_a_completion(local_llm: Any) -> None:
    payload = local_llm.complete("Reply with the single word: ok")

    choices = payload.get("choices")
    assert choices, f"no choices in response: {payload!r}"
    content = choices[0].get("message", {}).get("content")
    assert content, f"empty completion content: {payload!r}"
    assert payload.get("model"), f"provider did not report a model identity: {payload!r}"


@pytest.mark.docker
def test_local_llm_reports_usage(local_llm: Any) -> None:
    payload = local_llm.complete("Say hello", max_tokens=8)

    usage = payload.get("usage")
    assert usage is not None, f"provider did not report usage: {payload!r}"
    assert usage.get("prompt_tokens", 0) > 0, f"no prompt tokens reported: {payload!r}"
