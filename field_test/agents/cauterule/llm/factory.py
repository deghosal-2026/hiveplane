"""LLM provider factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cauterule.llm.provider import (
    AnthropicProvider,
    LiteLLMProvider,
    LLMProvider,
    OllamaProvider,
    OpenAIProvider,
)

if TYPE_CHECKING:
    from cauterule.config import Config


_PROVIDER_MAP: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
    "litellm": LiteLLMProvider,
}


def get_llm(config: Config) -> LLMProvider:
    """Return an :class:`LLMProvider` instance for *config*.

    Args:
        config: Loaded :class:`Config` (via :func:`cauterule.config.load_config`).

    Raises:
        ValueError: If ``config.llm.provider`` is unknown.
    """
    provider = config.llm.provider.strip().lower()
    if provider not in _PROVIDER_MAP:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Supported: {sorted(_PROVIDER_MAP)}")

    # Pass relevant config fields; each provider ignores extras it does not need.
    llm = config.llm
    if provider == "openai":
        return OpenAIProvider(
            model=llm.model,
            api_key=llm.api_key,
            base_url=llm.base_url,
            temperature=llm.temperature,
            timeout=llm.timeout,
            max_retries=llm.max_retries,
        )
    if provider == "anthropic":
        return AnthropicProvider(
            model=llm.model,
            api_key=llm.api_key,
            base_url=llm.base_url,
            temperature=llm.temperature,
            max_tokens=llm.max_tokens,
            timeout=llm.timeout,
            max_retries=llm.max_retries,
        )
    if provider == "ollama":
        return OllamaProvider(
            model=llm.model,
            base_url=llm.base_url or "http://localhost:11434",
            temperature=llm.temperature,
            timeout=llm.timeout,
            max_retries=llm.max_retries,
        )
    # litellm
    return LiteLLMProvider(
        model=llm.model,
        api_key=llm.api_key,
        base_url=llm.base_url,
        temperature=llm.temperature,
        timeout=llm.timeout,
        max_retries=llm.max_retries,
    )
