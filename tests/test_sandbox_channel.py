"""Tests for the token-guarded sandbox channel (M23, #110).

The channel is how a sandboxed child process reaches the control-plane boundary:
tool calls route through the ToolGateway, model calls through the provider seam,
and usage/result/state flow to the RunService. Every endpoint requires the
per-run token.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.api.sandbox_channel import SandboxChannel
from hiveplane.budget.pricing import CostTable
from hiveplane.core.manifest import load_manifest
from hiveplane.core.run import RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.llm.fake import FakeProvider
from hiveplane.registry.seeding import derive_tool_registrations, seed_tools
from hiveplane.registry.service import RegistryService

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_MODEL = "openai/gpt-4o/2024-08-06"
_TOKEN = "X-HivePlane-Run-Token"


def _seed_registry(registry: RegistryService, name: str) -> AgentWorkload:
    manifest = load_manifest(
        Path(__file__).resolve().parents[1] / "examples" / "workloads" / "repo-agent.yaml"
    )
    manifest = manifest.model_copy(
        update={"metadata": manifest.metadata.model_copy(update={"name": name})}
    )
    seed_tools(
        registry, derive_tool_registrations(manifest), clock=lambda: _NOW
    )
    registry.create(manifest)
    return manifest


def _app() -> tuple[TestClient, str, SandboxChannel]:
    os.environ["HIVEPLANE_EXECUTION__TOOL_FIXTURES"] = str(
        Path(__file__).resolve().parents[1] / "deploy" / "testdata" / "tools"
    )
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    channel = SandboxChannel()
    app.state.sandbox_channel = channel
    app.state.provider = FakeProvider()
    app.state.cost_table = CostTable()
    registry: RegistryService = app.state.registry_service
    _seed_registry(registry, "repo-agent")
    run = app.state.run_service.submit(
        workload="repo-agent",
        caller="cli",
        context="sandbox",
        model_identity=_MODEL,
    )
    app.state.run_service.start(run.id, actor="cli")
    return TestClient(app), run.id, channel


def test_tool_call_is_authorized_by_token() -> None:
    client, run_id, channel = _app()
    token = channel.mint(run_id)

    response = client.post(
        f"/internal/sandbox/{run_id}/tool-call",
        headers={_TOKEN: token},
        json={"tool_id": "mcp.github.list_pull_requests"},
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "allowed"


def test_tool_call_without_a_valid_token_is_rejected() -> None:
    client, run_id, _ = _app()

    response = client.post(
        f"/internal/sandbox/{run_id}/tool-call",
        headers={_TOKEN: "wrong"},
        json={"tool_id": "mcp.github.list_pull_requests"},
    )

    assert response.status_code == 401


def test_model_call_routes_through_the_provider_and_records_usage() -> None:
    client, run_id, channel = _app()
    token = channel.mint(run_id)

    response = client.post(
        f"/internal/sandbox/{run_id}/model",
        headers={_TOKEN: token},
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model_identity"] == _MODEL
    assert body["content"]


def test_result_endpoint_completes_the_run() -> None:
    client, run_id, channel = _app()
    token = channel.mint(run_id)

    response = client.post(
        f"/internal/sandbox/{run_id}/result",
        headers={_TOKEN: token},
        json={"result": {"risk": "low"}},
    )

    assert response.status_code == 200
    assert response.json()["state"] == RunState.COMPLETED.value


def test_control_endpoint_reports_state() -> None:
    client, run_id, channel = _app()
    token = channel.mint(run_id)

    response = client.get(f"/internal/sandbox/{run_id}/control", headers={_TOKEN: token})

    assert response.status_code == 200
    assert response.json()["state"] == RunState.RUNNING.value
    assert response.json()["cancelled"] is False
