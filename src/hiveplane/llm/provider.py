"""The provider protocol shared by all LLM backends (M23, #107)."""

from __future__ import annotations

from typing import Protocol

from hiveplane.llm.models import CompletionRequest, CompletionResponse


class LLMProvider(Protocol):
    """Invokes a model and returns a provider-neutral response."""

    def complete(self, request: CompletionRequest) -> CompletionResponse: ...
