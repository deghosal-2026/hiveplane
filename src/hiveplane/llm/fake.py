"""Deterministic fake/replay LLM provider for CI and tests (M23, #107)."""

from __future__ import annotations

from hiveplane.llm.models import (
    CompletionRequest,
    CompletionResponse,
    TokenUsage,
)


def _last_user_content(request: CompletionRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return message.content
    return request.messages[-1].content


def _count_tokens(text: str) -> int:
    return max(1, len(text.split()))


class FakeProvider:
    """A deterministic provider that never touches the network.

    Returns a replayed response when the last user message is in ``replay``,
    otherwise an echo of that message. Token counts are derived from text length
    so usage and cost are exercised without a live model.
    """

    def __init__(self, replay: dict[str, str] | None = None) -> None:
        self._replay = dict(replay or {})

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Return a deterministic completion for ``request``."""
        prompt = _last_user_content(request)
        content = self._replay.get(prompt, f"fake:{prompt}")
        input_tokens = _count_tokens(" ".join(m.content for m in request.messages))
        output_tokens = _count_tokens(content)
        return CompletionResponse(
            content=content,
            model_identity=request.model,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            finish_reason="stop",
        )
