"""Tests for the LLM provider factory (M23, #107)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from hiveplane.config import ModelSettings, Settings
from hiveplane.llm.factory import build_provider
from hiveplane.llm.fake import FakeProvider
from hiveplane.llm.models import CompletionRequest, Message
from hiveplane.llm.openai import OpenAICompatibleProvider


def test_builds_fake_provider_by_default() -> None:
    provider = build_provider(Settings(model=ModelSettings(provider="fake")))

    assert isinstance(provider, FakeProvider)


def test_builds_local_provider_from_base_url() -> None:
    provider = build_provider(
        Settings(
            model=ModelSettings(
                provider="local", base_url="http://ollama:11434/v1"
            )
        )
    )

    assert isinstance(provider, OpenAICompatibleProvider)


def test_local_provider_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        build_provider(Settings(model=ModelSettings(provider="local", base_url=None)))


def test_builds_cloud_provider_from_api_key() -> None:
    provider = build_provider(
        Settings(model=ModelSettings(provider="cloud", api_key=SecretStr("sk-test")))
    )

    assert isinstance(provider, OpenAICompatibleProvider)


def test_cloud_provider_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        build_provider(Settings(model=ModelSettings(provider="cloud", api_key=None)))


def test_fake_provider_loads_replay_file(tmp_path: Path) -> None:
    replay_file = tmp_path / "replay.json"
    replay_file.write_text(json.dumps({"hello": "risk: low"}))

    provider = build_provider(
        Settings(model=ModelSettings(provider="fake", replay_file=str(replay_file)))
    )
    response = provider.complete(
        CompletionRequest(messages=[Message(role="user", content="hello")], model="fake/echo/1")
    )

    assert response.content == "risk: low"
