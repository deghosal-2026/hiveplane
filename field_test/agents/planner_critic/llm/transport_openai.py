"""OpenAI-compatible transport (F-22, PRD §2.4): the first concrete provider.

Implements the Chat Completions API with JSON mode over ``httpx``. ``base_url``
is overridable so the same code reaches OMLX, Ollama, vLLM, OpenRouter, or
OpenAI (the registry supplies the configured endpoint). ``api_key`` is
optional — local endpoints need none, and the default config points at local
models so no paid provider is ever hit on the default path.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx

from .base import (
    BadJSONError,
    Completion,
    Message,
    ProviderTimeout,
    ToolSchema,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 180.0
DEFAULT_MAX_TOKENS = 16384


class OpenAICompatibleProvider:
    """A provider speaking the OpenAI Chat Completions protocol (F-22).

    Args:
        name: Provider id (from the registry).
        base_url: Base URL, e.g. ``http://localhost:11434/v1`` or OpenAI.
        model: Model name to request.
        api_key: Optional bearer key for paid/authenticated endpoints.
        timeout: Per-request timeout in seconds.
        max_tokens: Max response tokens (default 16384; env: ``PC_MAX_TOKENS``).
        client: Optional ``httpx.Client`` (tests inject a ``MockTransport``).
        extra_payload: Optional dict of provider-specific extras merged into
            the chat-completions payload (e.g. vLLM's ``chat_template_kwargs``).
            Default: ``{}`` (strict OpenAI-compatible). Use ``enable_thinking``
            on the provider spec to enable vLLM thinking suppression.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_tokens: int | None = None,
        client: httpx.Client | None = None,
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        """Configure the transport."""
        import os

        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._timeout = timeout
        if max_tokens is not None:
            self._max_tokens = max_tokens
        else:
            try:
                self._max_tokens = int(os.environ.get("PC_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
            except ValueError:
                self._max_tokens = DEFAULT_MAX_TOKENS
        self._client = client if client is not None else httpx.Client()
        self._extra_payload = extra_payload or {}

    def complete(
        self,
        messages: Sequence[Message],
        tool_schemas: Sequence[ToolSchema] = (),
    ) -> Completion:
        """POST to the chat completions endpoint and parse the completion.

        Args:
            messages: The conversation.
            tool_schemas: Tool definitions to send (JSON mode does not use them).

        Returns:
            A structured completion.

        Raises:
            ProviderTimeout: if the endpoint times out.
            BadJSONError: if the endpoint returns unparseable content.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, object] = {
            "model": self.model,
            "messages": [m.model_dump() for m in messages],
            "response_format": {"type": "json_object"},
            "max_tokens": self._max_tokens,
        }
        payload.update(self._extra_payload)
        if tool_schemas:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tool_schemas
            ]

        logger.info(
            "provider '%s' POST chat/completions — model=%s tokens=%d",
            self.name,
            self.model,
            self._max_tokens,
        )
        try:
            resp = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as err:
            logger.error("provider '%s' timed out after %ss", self.name, self._timeout)
            raise ProviderTimeout(
                f"provider '{self.name}' timed out after {self._timeout}s"
            ) from err
        except httpx.HTTPError as err:
            logger.error("provider '%s' transport error: %s", self.name, err)
            raise ProviderTimeout(f"provider '{self.name}' transport error: {err}") from err

        if resp.status_code >= 400:
            logger.error(
                "provider '%s' HTTP %d — model=%s body=%s",
                self.name,
                resp.status_code,
                self.model,
                resp.text[:500],
            )
            raise ProviderTimeout(
                f"provider '{self.name}' returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            finish_reason = data["choices"][0].get("finish_reason", "stop")
        except (KeyError, IndexError, ValueError) as err:
            logger.error(
                "provider '%s' malformed response — model=%s raw=%s",
                self.name,
                self.model,
                resp.text[:1000],
            )
            raise BadJSONError(f"provider '{self.name}' returned malformed response shape") from err

        if not isinstance(content, str):
            raise BadJSONError(f"provider '{self.name}' returned non-string content")

        logger.info(
            "provider '%s' completed — model=%s finish=%s content_len=%d",
            self.name,
            self.model,
            finish_reason,
            len(content),
        )

        if finish_reason != "stop":
            raise BadJSONError(
                f"provider '{self.name}' returned truncated response "
                f"(finish_reason={finish_reason})"
            )
        return Completion(content=content, finish_reason=str(finish_reason))
