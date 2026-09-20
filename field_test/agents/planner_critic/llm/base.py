"""The LLM provider layer (F-20..F-24, PRD §2.4): protocol + envelope.

The provider surface is a thin, transport-agnostic seam: a provider
``complete``s a message list and returns a *structured* response that the
structured-output enforcer (:mod:`planner_critic.llm.structured`) re-validates
against the Goal/Plan/Finding schemas. This module owns the protocol, the
response envelope, and the fail-closed error types so that M2's concrete
transports (OpenAI-compatible, and later Anthropic/Gemini) share one contract
and one set of failure modes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    """One chat message speaking plain typed JSON (agnostic by design)."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class ToolSchema(BaseModel):
    """A tool a model may call, described in OpenAI-compatible shape."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Tool name")
    description: str = Field(default="", description="What the tool does")
    parameters: dict[str, Any] = Field(default_factory=dict, description="JSON Schema")


class Completion(BaseModel):
    """A structured completion from a provider.

    Content is a string the enforcer parses as JSON and validates against a
    typed schema; arbitrary structured output (function calls, tool results)
    is carried out-of-band so the transport stays minimal.
    """

    model_config = ConfigDict(frozen=True)

    content: str
    finish_reason: str = "stop"


class ProviderError(Exception):
    """Base class for fail-closed provider failures (F-70).

    Raising a subclass of :class:`ProviderError` signals the loop to surface
    ``planning_unavailable`` for the affected role rather than handing garbage
    upstream. The message is safe to show a user (no secrets).
    """


class ProviderTimeout(ProviderError):
    """A provider call exceeded its time budget."""


class BadJSONError(ProviderError):
    """A provider returned content that is not parseable JSON."""


class SchemaMismatchError(ProviderError):
    """A provider returned valid-looking JSON that fails schema validation."""


@runtime_checkable
class LLMProvider(Protocol):
    """A transport that turns messages into a structured completion (F-20).

    Implementations are configured (name/base_url/model/api_key) by the
    registry, never hard-coded in the engine. A real transport raises
    :class:`ProviderError` subclasses on failure; a fake provider in tests
    simply returns a fixed :class:`Completion`.
    """

    name: str
    base_url: str
    model: str

    def complete(
        self,
        messages: Sequence[Message],
        tool_schemas: Sequence[ToolSchema] = (),
    ) -> Completion:
        """Send messages and return a structured completion.

        Args:
            messages: The conversation so far.
            tool_schemas: Optional tool definitions for the model to call.

        Returns:
            The model's completion.

        Raises:
            ProviderError: on timeout, malformed output, or transport failure.
        """
        ...
