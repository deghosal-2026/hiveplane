"""LLM provider layer package (F-20..F-24).

A config-driven, transport-agnostic seam: the :class:`LLMProvider` protocol,
the structured-output enforcer, the registry, and the first concrete transport
(OpenAI-compatible). The engine only ever talks to the protocol — providers are
loaded from config, never imported by the core.
"""

from __future__ import annotations

from .base import (
    BadJSONError,
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    ProviderTimeout,
    SchemaMismatchError,
    ToolSchema,
)
from .registry import ProviderRegistry, ProviderSpec
from .structured import StructuredEnforcer
from .transport_openai import OpenAICompatibleProvider

__all__ = [
    "BadJSONError",
    "Completion",
    "LLMProvider",
    "Message",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderRegistry",
    "ProviderSpec",
    "ProviderTimeout",
    "SchemaMismatchError",
    "StructuredEnforcer",
    "ToolSchema",
]
