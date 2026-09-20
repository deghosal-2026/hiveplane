"""Container entrypoint: uvicorn + FastAPI for the HTTP surface (M8).

Builds a provider config from PC_* env, binds planner/critic roles, and
serves :func:`~planner_critic.server.http.create_fastapi_app` on PC_PORT.

Env vars:
    PC_OPENAI_BASE_URL  — LLM endpoint (default: https://openrouter.ai/api/v1)
    PC_OPENAI_MODEL     — model name (e.g. deepseek/deepseek-v4-flash)
    PC_OPENAI_API_KEY   — bearer key for authenticated endpoints
    PC_REVISION_CAP     — loop revision cap (default: 3)
    PC_CRITIQUE_MODE    — heuristic-only|deterministic-first|llm-every-revision
    PC_MAX_TOKENS       — max response tokens (default: 16384)
    PC_STORE            — SQLite database path
    PC_CONFIG           — provider TOML config path
    PC_PORT             — HTTP listen port
    PC_LOG_LEVEL        — logging level (default: INFO)
"""

from __future__ import annotations

import logging
import os

from ..llm.registry import ProviderRegistry

DEFAULT_PORT = "8080"
DEFAULT_STORE = "/data/plans.db"
DEFAULT_CONFIG = "/data/plancritic.toml"


def _setup_logging() -> None:
    """Configure root logging so loop/transport progress is visible in containers."""
    level = os.environ.get("PC_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def bootstrap_config() -> None:
    """Write a plancritic.toml binding planner/critic to the configured provider."""
    base_url = os.environ.get("PC_OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("PC_OPENAI_MODEL", "")
    api_key = os.environ.get("PC_OPENAI_API_KEY") or None
    config = os.environ.get("PC_CONFIG", DEFAULT_CONFIG)
    registry = ProviderRegistry.load(config)
    registry.add("local", base_url=base_url, model=model, api_key=api_key, role="planner")
    registry.add("local", base_url=base_url, model=model, api_key=api_key, role="critic")
    registry.save()


def main() -> None:
    _setup_logging()
    bootstrap_config()
    import uvicorn

    from .http import create_fastapi_app

    store = os.environ.get("PC_STORE", DEFAULT_STORE)
    config = os.environ.get("PC_CONFIG", DEFAULT_CONFIG)
    port = int(os.environ.get("PC_PORT", DEFAULT_PORT))
    log_level = os.environ.get("PC_LOG_LEVEL", "info").lower()
    app = create_fastapi_app(store, config_path=config)
    if app is None:
        raise RuntimeError("fastapi extra not installed")
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - container binding to all interfaces
        port=port,
        log_level=log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
