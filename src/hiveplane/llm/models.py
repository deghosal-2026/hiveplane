"""Provider-neutral request/response models for the LLM seam (M23, #107)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Message(BaseModel):
    """A single chat message."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    """Token counts reported by a provider."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens


class CompletionRequest(BaseModel):
    """A provider-neutral model invocation request."""

    model_config = ConfigDict(extra="forbid")

    messages: list[Message] = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout_s: float | None = Field(default=None, gt=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CompletionResponse(BaseModel):
    """A provider-neutral model invocation response."""

    model_config = ConfigDict(extra="forbid")

    content: str
    model_identity: str = Field(min_length=1)
    usage: TokenUsage
    finish_reason: str = "stop"
