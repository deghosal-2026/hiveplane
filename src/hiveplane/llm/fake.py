"""Deterministic fake/replay LLM provider for CI and tests (M23, #107, #137)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from hiveplane.llm.models import (
    CompletionRequest,
    CompletionResponse,
    TokenUsage,
)


class ReplayMissingError(Exception):
    """Raised when a configured replay has no entry for a request."""

    def __init__(self, key: str) -> None:
        super().__init__(f"no replay entry for request key {key!r}")
        self.key = key


def replay_key(request: CompletionRequest) -> str:
    """Return the stable sha256 key for a completion request (M23, #137).

    The key covers the model identity, every message, and the temperature, so
    two corpus tasks that build different prompts map to different replay
    entries. ``max_tokens``/``timeout_s``/``metadata`` are excluded because the
    example agents leave them unset.
    """
    payload = {
        "model": request.model,
        "temperature": request.temperature,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_user_content(request: CompletionRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return message.content
    return request.messages[-1].content


def _count_tokens(text: str) -> int:
    return max(1, len(text.split()))


class FakeProvider:
    """A deterministic provider that never touches the network.

    With a configured ``replay`` map (keyed by :func:`replay_key`), every
    request must have an entry — a missing entry raises
    :class:`ReplayMissingError` so certification never runs against silent
    stand-in content. Without a replay map the provider echoes the last user
    message (dev convenience only, never used by certification).
    """

    def __init__(self, replay: dict[str, str] | None = None) -> None:
        self._replay = dict(replay) if replay is not None else None

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Return a deterministic completion for ``request``."""
        if self._replay is not None:
            key = replay_key(request)
            entry: Any = self._replay.get(key)
            if entry is None:
                raise ReplayMissingError(key)
            content = str(entry)
        else:
            prompt = _last_user_content(request)
            content = f"fake:{prompt}"
        input_tokens = _count_tokens(" ".join(m.content for m in request.messages))
        output_tokens = _count_tokens(content)
        return CompletionResponse(
            content=content,
            model_identity=request.model,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            finish_reason="stop",
        )
