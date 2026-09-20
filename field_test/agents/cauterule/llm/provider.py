"""LLM provider abstraction.

Unified interface for OpenAI, Anthropic, Ollama, and LiteLLM.
Each provider lazily imports its SDK so the package remains installable
without optional LLM dependencies.
"""

from __future__ import annotations

import abc
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMResponse:
    """Response from an LLM provider."""

    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _require_optional(package: str, extra: str) -> None:
    """Import *package*, raising an actionable error if missing (#507).

    Raises:
        ImportError: Naming the exact extra to install.
    """
    try:
        __import__(package)
    except ImportError:
        msg = f"{package} is not installed — pip install cauterule[{extra}]"
        raise ImportError(msg) from None


# Substrings marking a failure as worth retrying (#508).
_TRANSIENT_HINTS = (
    "timeout",
    "timed out",
    "rate limit",
    "ratelimit",
    "overloaded",
    "service unavailable",
    "bad gateway",
    "connection",
    "temporarily",
    "try again",
)


def _is_transient(exc: BaseException) -> bool:
    """Return True if *exc* looks like a transient provider failure (#508).

    Timeouts are treated as transient (retryable) — a network timeout is
    usually transient for cloud providers. The field-test runner additionally
    bounds each trajectory with a per-future timeout (#713) so a local model
    that hangs on a pathological prompt cannot block the corpus even if the
    provider retries.
    """
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(hint in text for hint in _TRANSIENT_HINTS)


def _call_with_retries(fn: Callable[[], Any], max_retries: int) -> Any:
    """Call *fn*, retrying transient failures with exponential backoff (#508)."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if attempt >= max_retries or not _is_transient(exc):
                raise
            time.sleep(0.5 * (2**attempt))
            attempt += 1


class LLMProvider(abc.ABC):
    """Abstract LLM provider."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider name."""

    @abc.abstractmethod
    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """Generate a completion for *prompt*."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


class OpenAIProvider(LLMProvider):
    """OpenAI provider (requires ``openai``)."""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str = "",
        temperature: float = 0.5,
        timeout: float = 30,
        max_retries: int = 2,
        max_tokens: int = 4096,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._temperature = temperature
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "openai"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        _require_optional("openai", "llm")
        import openai

        timeout = kwargs.get("timeout", self._timeout)
        client_kwargs: dict[str, Any] = {"timeout": timeout}
        if self._api_key:
            client_kwargs["api_key"] = self._api_key
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        temperature = kwargs.get("temperature", self._temperature)
        max_tokens = kwargs.get("max_tokens", self._max_tokens)
        client = openai.OpenAI(**client_kwargs)

        def _call() -> Any:
            return client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )

        resp = _call_with_retries(_call, int(kwargs.get("max_retries", self._max_retries)))
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text,
            model=self._model,
            provider=self.name,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )


class AnthropicProvider(LLMProvider):
    """Anthropic provider (requires ``anthropic``)."""

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-20241022",
        api_key: str = "",
        base_url: str = "",
        temperature: float = 0.5,
        max_tokens: int = 4096,
        timeout: float = 30,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        _require_optional("anthropic", "llm")
        import anthropic

        client_kwargs: dict[str, Any] = {"timeout": kwargs.get("timeout", self._timeout)}
        if self._api_key:
            client_kwargs["api_key"] = self._api_key
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        temperature = kwargs.get("temperature", self._temperature)
        max_tokens = kwargs.get("max_tokens", self._max_tokens)
        client = anthropic.Anthropic(**client_kwargs)

        def _call() -> Any:
            return client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )

        resp = _call_with_retries(_call, int(kwargs.get("max_retries", self._max_retries)))
        text = "".join(
            block.text if hasattr(block, "text") else str(block) for block in resp.content
        )
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text,
            model=self._model,
            provider=self.name,
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )


class OllamaProvider(LLMProvider):
    """Ollama provider (local, requires ``ollama`` server)."""

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.5,
        timeout: float = 30,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._temperature = temperature
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def name(self) -> str:
        return "ollama"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        _require_optional("requests", "llm")
        import requests

        temperature = kwargs.get("temperature", self._temperature)
        timeout = kwargs.get("timeout", self._timeout)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "temperature": temperature,
            "stream": False,
        }

        def _call() -> dict[str, Any]:
            resp = requests.post(
                f"{self._base_url}/api/generate",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            return body

        body = _call_with_retries(_call, int(kwargs.get("max_retries", self._max_retries)))
        return LLMResponse(
            text=str(body.get("response", "")),
            model=self._model,
            provider=self.name,
            prompt_tokens=int(body.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(body.get("eval_count", 0) or 0),
        )


class LiteLLMProvider(LLMProvider):
    """LiteLLM provider (supports any LiteLLM-compatible model)."""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str = "",
        temperature: float = 0.5,
        timeout: float = 30,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._temperature = temperature
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def name(self) -> str:
        return "litellm"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        _require_optional("litellm", "llm")
        import litellm

        temperature = kwargs.get("temperature", self._temperature)
        completion_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "timeout": kwargs.get("timeout", self._timeout),
        }
        if self._api_key:
            completion_kwargs["api_key"] = self._api_key
        if self._base_url:
            completion_kwargs["api_base"] = self._base_url

        def _call() -> Any:
            return litellm.completion(**completion_kwargs)

        resp = _call_with_retries(_call, int(kwargs.get("max_retries", self._max_retries)))
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text,
            model=self._model,
            provider=self.name,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
