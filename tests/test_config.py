"""Tests for hiveplane.config — typed settings with env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from hiveplane.config import (
    CertificationSettings,
    CertificationThresholds,
    DatabaseSettings,
    RedisSettings,
    Settings,
    get_settings,
)


def test_defaults_load() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.debug is False
    assert settings.database.host == "localhost"
    assert settings.database.port == 5432
    assert settings.database.name == "hiveplane"
    assert settings.redis.port == 6379
    assert settings.otel.service_name == "hiveplane"
    assert settings.sandbox.defaults.memory_mb == 512
    assert settings.certification.production.min_pass_rate == 0.85


def test_database_url_is_built_from_parts() -> None:
    settings = Settings(
        database=DatabaseSettings(
            host="db.internal",
            port=5433,
            name="fleet",
            user="ops",
            password=SecretStr("s3cret"),
        )
    )

    assert settings.database.url == "postgresql+psycopg://ops:s3cret@db.internal:5433/fleet"


def test_password_is_masked_in_repr() -> None:
    settings = Settings(database=DatabaseSettings(password=SecretStr("s3cret")))

    assert isinstance(settings.database.password, SecretStr)
    assert "s3cret" not in repr(settings.database)
    assert settings.database.password.get_secret_value() == "s3cret"


def test_redis_url_without_password() -> None:
    settings = Settings(redis=RedisSettings(host="cache", port=6380, db=2))

    assert settings.redis.url == "redis://cache:6380/2"


def test_redis_url_with_password() -> None:
    settings = Settings(redis=RedisSettings(password=SecretStr("hunter2")))

    assert settings.redis.url == "redis://:hunter2@localhost:6379/0"


def test_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_ENVIRONMENT", "production")
    monkeypatch.setenv("HIVEPLANE_DATABASE__HOST", "prod-db")
    monkeypatch.setenv("HIVEPLANE_DATABASE__PORT", "6543")
    monkeypatch.setenv("HIVEPLANE_CERTIFICATION__PRODUCTION__MIN_PASS_RATE", "0.95")

    settings = Settings()

    assert settings.environment == "production"
    assert settings.database.host == "prod-db"
    assert settings.database.port == 6543
    assert settings.certification.production.min_pass_rate == 0.95


def test_dotenv_file_is_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("HIVEPLANE_DATABASE__HOST=from-dotenv\n")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.database.host == "from-dotenv"


def test_get_settings_returns_cached_instance() -> None:
    assert get_settings() is get_settings()


def test_invalid_trace_sampling_reports_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_OTEL__TRACE_SAMPLING", "2.0")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    assert "otel.trace_sampling" in str(excinfo.value)


def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_DATABASE__PORT", "99999")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    assert "database.port" in str(excinfo.value)


def test_invalid_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_ENVIRONMENT", "galaxy")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    assert "environment" in str(excinfo.value)


def test_production_threshold_must_meet_staging() -> None:
    with pytest.raises(ValidationError, match="production_threshold"):
        CertificationSettings(
            staging=CertificationThresholds(min_pass_rate=0.9),
            production=CertificationThresholds(min_pass_rate=0.8),
        )


def test_sandbox_defaults_are_typed() -> None:
    settings = Settings()

    assert settings.sandbox.defaults.cpu_cores == 1.0
    assert settings.sandbox.defaults.wall_clock_s == 300
    assert settings.sandbox.defaults.egress_mode == "restricted"
    assert settings.sandbox.defaults.filesystem == "isolated"


def test_fanout_defaults() -> None:
    settings = Settings()

    assert settings.fanout.enabled is True
    assert settings.fanout.timeout_s == 10.0
    assert settings.fanout.max_retries == 3
    assert settings.fanout.slack_webhook_url is None


def test_model_defaults() -> None:
    settings = Settings()

    assert settings.model.provider == "fake"
    assert settings.model.timeout_s == 60.0
    assert settings.model.api_key is None


def test_model_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_MODEL__PROVIDER", "local")
    monkeypatch.setenv("HIVEPLANE_MODEL__BASE_URL", "http://ollama:11434/v1")

    settings = Settings()

    assert settings.model.provider == "local"
    assert settings.model.base_url == "http://ollama:11434/v1"


def test_signing_key_file_defaults_to_none() -> None:
    settings = Settings()

    assert settings.certification.signing_key_file is None


def test_signing_key_file_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_CERTIFICATION__SIGNING_KEY_FILE", "/keys/cert.pem")

    settings = Settings()

    assert settings.certification.signing_key_file == "/keys/cert.pem"


def test_adapter_executor_is_valid() -> None:
    settings = Settings(certification=CertificationSettings(executor="adapter"))

    assert settings.certification.executor == "adapter"
