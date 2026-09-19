"""Tests for operator UI configuration (M22, #83)."""

from __future__ import annotations

import pytest

from hiveplane.config import Settings, get_settings


def test_ui_settings_defaults() -> None:
    settings = Settings()

    assert settings.ui.api_url == "http://localhost:8000"
    assert settings.ui.port == 8000


def test_ui_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_UI__API_URL", "http://api:9999")
    monkeypatch.setenv("HIVEPLANE_UI__PORT", "9000")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ui.api_url == "http://api:9999"
    assert settings.ui.port == 9000
