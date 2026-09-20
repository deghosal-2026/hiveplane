"""OpenAI-compatible provider for local (OMLX) and cloud endpoints (M23, #107, #141)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from hiveplane.llm.models import (
    CompletionRequest,
    CompletionResponse,
    TokenUsage,
)

#: Posts a JSON payload and returns the decoded JSON response.
Transport = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]

#: Per-attempt backoff base for transient failures (seconds).
_RETRY_BACKOFF_S = 0.05


def _is_retryable(exc: BaseException) -> bool:
    """Retry connection errors and 5xx/429; never retry other 4xx responses."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return True


def _urllib_transport(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout_s: float
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        decoded: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    return decoded


class OpenAICompatibleProvider:
    """Calls an OpenAI-compatible ``/chat/completions`` endpoint.

    Transient transport failures (connection errors, 5xx, 429) are retried up
    to ``max_retries`` times with linear backoff (M23, #141); a permanent HTTP
    error surfaces immediately so the run fails with an attributed reason.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        model_aliases: dict[str, str] | None = None,
        transport: Transport | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._aliases = dict(model_aliases or {})
        # Reverse view: canonical bound identity -> name the server can serve.
        self._request_models = {
            canonical: served for served, canonical in self._aliases.items()
        }
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._transport: Transport = transport or (
            lambda url, payload, headers: _urllib_transport(
                url, payload, headers, timeout_s
            )
        )

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Invoke the endpoint and return a provider-neutral response."""
        payload: dict[str, Any] = {
            "model": self._request_models.get(request.model, request.model),
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"

        data = self._send(f"{self._base_url}/chat/completions", payload, headers)
        choice = data["choices"][0]
        usage = data.get("usage", {})
        reported = str(data.get("model", request.model))
        return CompletionResponse(
            content=str(choice["message"]["content"]),
            model_identity=self._aliases.get(reported, reported),
            usage=TokenUsage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
            finish_reason=str(choice.get("finish_reason", "stop")),
        )

    def _send(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        """POST the payload, retrying transient transport failures."""
        for attempt in range(self._max_retries + 1):
            try:
                return self._transport(url, payload, headers)
            except Exception as exc:
                if not _is_retryable(exc) or attempt == self._max_retries:
                    raise
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover
