"""Tests for the LLM provider factory (M23, #107)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from hiveplane.config import ModelSettings, Settings
from hiveplane.llm.factory import build_provider
from hiveplane.llm.fake import FakeProvider, ReplayMissingError, replay_key
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
    request = CompletionRequest(
        messages=[Message(role="user", content="hello")], model="fake/echo/1"
    )
    replay_file = tmp_path / "replay.json"
    replay_file.write_text(json.dumps({replay_key(request): "risk: low"}))

    provider = build_provider(
        Settings(model=ModelSettings(provider="fake", replay_file=str(replay_file)))
    )
    response = provider.complete(request)

    assert response.content == "risk: low"


def test_fake_provider_replay_file_requires_a_matching_entry(tmp_path: Path) -> None:
    replay_file = tmp_path / "replay.json"
    replay_file.write_text(json.dumps({"not-a-request-key": "risk: low"}))

    provider = build_provider(
        Settings(model=ModelSettings(provider="fake", replay_file=str(replay_file)))
    )

    with pytest.raises(ReplayMissingError):
        provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="hello")], model="fake/echo/1"
            )
        )
