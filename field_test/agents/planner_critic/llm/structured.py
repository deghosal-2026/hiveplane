"""Structured-output enforcement (F-24, F-70): schema-validate provider JSON.

A provider's free-form completion is re-validated against a typed Pydantic
schema (Goal / PlanVersion / Finding) before it may flow into the engine. On a
schema mismatch the call is retried a bounded number of times; a persistent
mismatch raises :class:`~planner_critic.types.PlanningError` so the loop
surfaces ``planning_unavailable`` for the affected role (fail-closed, F-70).
"""

from __future__ import annotations

import json
from typing import TypeVar, cast

from pydantic import BaseModel

from ..types import PlanningError
from .base import BadJSONError, LLMProvider, Message, ProviderError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MAX_RETRIES = 2


class StructuredEnforcer:
    """Wraps a provider so every completion is schema-validated (F-24).

    Args:
        provider: The transport to call.
        max_retries: Bounded retries before fail-closed (F-70).
    """

    def __init__(
        self,
        provider: LLMProvider,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Bind the provider and retry budget."""
        self.provider = provider
        self.max_retries = max_retries

    def complete(self, messages: list[Message], schema: type[T]) -> T:
        """Call the provider and return a schema-validated instance.

        Args:
            messages: The conversation to send.
            schema: The typed Pydantic model completions must validate against.

        Returns:
            A validated instance of ``schema``.

        Raises:
            PlanningError: on persistent schema mismatch (fail-closed).
        """
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                completion = self.provider.complete(messages)
            except ProviderError as err:
                last_error = err
                continue
            try:
                parsed = _parse_json(completion.content)
            except BadJSONError as err:
                last_error = err
                continue
            try:
                return schema.model_validate(parsed)
            except Exception as err:  # ValidationError subclasses Exception
                last_error = err
                continue
        raise PlanningError(
            f"provider '{self.provider.name}' failed to return valid "
            f"{schema.__name__} after {self.max_retries + 1} attempts: {last_error}",
            reason_code="planning_unavailable",
        )


def _parse_json(content: str) -> dict[str, object]:
    """Parse a completion payload, tolerating markdown code fences.

    Args:
        content: The provider's raw completion text.

    Returns:
        The parsed JSON object.

    Raises:
        BadJSONError: if the content is not a JSON object.
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[len("json") :].lstrip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as err:
        raise BadJSONError(f"completion is not valid JSON: {err}") from err
    if not isinstance(parsed, dict):
        raise BadJSONError("completion JSON must be an object")
    return cast("dict[str, object]", parsed)
