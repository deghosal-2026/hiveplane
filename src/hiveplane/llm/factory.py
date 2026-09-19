"""Builds the configured LLM provider (M23, #107)."""

from __future__ import annotations

import json
from pathlib import Path

from hiveplane.config import ModelSettings, Settings, get_settings
from hiveplane.llm.fake import FakeProvider
from hiveplane.llm.openai import OpenAICompatibleProvider
from hiveplane.llm.provider import LLMProvider

_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _load_replay(path: str) -> dict[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in data.items()}


def _build(model: ModelSettings) -> LLMProvider:
    if model.provider == "fake":
        replay = _load_replay(model.replay_file) if model.replay_file else None
        return FakeProvider(replay=replay)
    if model.provider == "local":
        if not model.base_url:
            raise ValueError("model.base_url is required when provider is 'local'")
        return OpenAICompatibleProvider(
            base_url=model.base_url,
            model_aliases=model.model_aliases,
            timeout_s=model.timeout_s,
        )
    if model.api_key is None:
        raise ValueError("model.api_key is required when provider is 'cloud'")
    return OpenAICompatibleProvider(
        base_url=model.base_url or _OPENAI_BASE_URL,
        api_key=model.api_key.get_secret_value(),
        model_aliases=model.model_aliases,
        timeout_s=model.timeout_s,
    )


def build_provider(settings: Settings | None = None) -> LLMProvider:
    """Build the provider selected by settings (defaults to the process settings)."""
    return _build((settings or get_settings()).model)
