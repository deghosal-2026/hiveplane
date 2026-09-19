"""Tests for the scaffolded hello-agent worker (M23, #119)."""

from __future__ import annotations

from typing import Any

from examples.worker import run

from hiveplane.llm.models import CompletionResult, TokenUsage


class _FakeCtx:
    """A minimal WorkerContext stand-in exposing the LLM seam."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str, **options: Any) -> CompletionResult:
        self.prompts.append(prompt)
        return CompletionResult(
            content="a friendly greeting",
            model_identity="fake/echo/1",
            usage=TokenUsage(input_tokens=3, output_tokens=2),
            finish_reason="stop",
        )


def test_hello_worker_returns_deterministic_greeting() -> None:
    ctx = _FakeCtx()

    result = run({"name": "world"}, ctx)  # type: ignore[arg-type]

    assert result["greeting"] == "hello, world"


def test_hello_worker_calls_the_model_seam() -> None:
    ctx = _FakeCtx()

    result = run({"name": "world"}, ctx)  # type: ignore[arg-type]

    assert ctx.prompts
    assert result["note"] == "a friendly greeting"
