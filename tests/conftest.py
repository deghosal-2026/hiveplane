"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from hiveplane.config import get_settings
from hiveplane.core.manifest import parse_manifest
from hiveplane.core.workload import AgentWorkload
from postgres import postgres_engine


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Run each test in a clean cwd with no HIVEPLANE_* env vars or cached settings."""
    for key in list(os.environ):
        if key.startswith("HIVEPLANE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_manifest() -> Callable[..., AgentWorkload]:
    """Return a factory that builds valid AgentWorkload instances."""
    counter = {"n": 0}

    def _make(
        name: str | None = None,
        owner: str = "platform-team",
        team: str = "platform",
        *,
        adapter: str = "raw-worker",
        status: str = "uncertified",
        **spec_overrides: Any,
    ) -> AgentWorkload:
        counter["n"] += 1
        workload_name = name if name is not None else f"agent-{counter['n']}"
        spec: dict[str, Any] = {
            "runtime": {"adapter": adapter, "entrypoint": "examples.worker:run"},
            "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0, "per_team_usd": 50.0},
            "model": {
                "strategy": "tiered",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
            "certification": {
                "benchmark_corpus": "corpora/x/v1",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
                "status": status,
            },
        }
        spec.update(spec_overrides)
        return parse_manifest(
            {
                "apiVersion": "hiveplane/v1",
                "kind": "AgentWorkload",
                "metadata": {"name": workload_name, "owner": owner, "team": team},
                "spec": spec,
            }
        )

    return _make


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    """Provide a live Postgres engine, skipping the test when unavailable."""
    engine = postgres_engine()
    try:
        yield engine
    finally:
        engine.dispose()
