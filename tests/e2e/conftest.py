"""Live control-plane + operator-UI stack for browser E2E tests (M22, #91)."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn

from e2e.live_stack import LiveStack
from hiveplane.api.app import create_app
from hiveplane.config import get_settings
from hiveplane.core.decision import ActionClass
from hiveplane.ui.app import create_ui_app
from hiveplane.ui.client import HttpControlPlaneClient

_WORKLOAD = "e2e-agent"
_MANIFEST: dict[str, Any] = {
    "apiVersion": "hiveplane/v1",
    "kind": "AgentWorkload",
    "metadata": {"name": _WORKLOAD, "owner": "e2e", "team": "platform"},
    "spec": {
        "runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"},
        "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0, "per_team_usd": 50.0},
        "model": {
            "strategy": "tiered",
            "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
        },
        "certification": {
            "benchmark_corpus": "corpora/e2e/v1",
            "staging_threshold": 0.8,
            "production_threshold": 0.9,
            "status": "uncertified",
        },
    },
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server:
    """Run a FastAPI app under uvicorn on a background thread."""

    def __init__(self, app: Any, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _wait(url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError(f"server at {url} did not become ready")


def _submit_and_start(client: httpx.Client) -> str:
    run = client.post(
        "/runs",
        json={"workload": _WORKLOAD, "caller": "e2e", "context": "sandbox", "task": {}},
    )
    run.raise_for_status()
    run_id = str(run.json()["id"])
    client.post(f"/runs/{run_id}/start").raise_for_status()
    return run_id


@pytest.fixture(autouse=True)
def _isolated_settings() -> None:
    """E2E tests drive a live server; per-test settings isolation is disabled."""


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveStack]:
    """Start the control plane and UI, and seed a scenario."""
    os.environ["HIVEPLANE_EXECUTION__DATA_DIR"] = str(tmp_path_factory.mktemp("e2e-runs"))
    get_settings.cache_clear()

    api_app = create_app()
    api_port = _free_port()
    api_server = _Server(api_app, api_port)
    api_server.start()
    api_url = f"http://127.0.0.1:{api_port}"
    _wait(f"{api_url}/healthz")

    ui_app = create_ui_app(HttpControlPlaneClient(api_url))
    ui_port = _free_port()
    ui_server = _Server(ui_app, ui_port)
    ui_server.start()
    ui_url = f"http://127.0.0.1:{ui_port}"
    _wait(f"{ui_url}/healthz")

    with httpx.Client(base_url=api_url, timeout=5.0) as client:
        client.post("/workloads", json=_MANIFEST).raise_for_status()

        run_running = _submit_and_start(client)

        run_paused = _submit_and_start(client)
        client.post(f"/runs/{run_paused}/pause").raise_for_status()

        run_paused_deny = _submit_and_start(client)
        client.post(f"/runs/{run_paused_deny}/pause").raise_for_status()

    approvals = api_app.state.approval_service
    approval_approve = approvals.request(
        run_id=run_paused,
        workload=_WORKLOAD,
        rule="e2e-destructive",
        reason="seeded for approval flow",
        action_class=ActionClass.DESTRUCTIVE,
    )
    approval_deny = approvals.request(
        run_id=run_paused_deny,
        workload=_WORKLOAD,
        rule="e2e-destructive",
        reason="seeded for denial flow",
        action_class=ActionClass.DESTRUCTIVE,
    )

    yield LiveStack(
        api_url=api_url,
        ui_url=ui_url,
        workload=_WORKLOAD,
        run_running=run_running,
        approval_approve=approval_approve.approval_id,
        approval_deny=approval_deny.approval_id,
    )

    ui_app.state.control_plane.close()
    ui_server.stop()
    api_server.stop()


@pytest.fixture(scope="session")
def dead_ui_url() -> Iterator[str]:
    """A UI server pointed at a dead control plane, for the 502 flow."""
    ui_app = create_ui_app(HttpControlPlaneClient("http://127.0.0.1:1"))
    port = _free_port()
    server = _Server(ui_app, port)
    server.start()
    ui_url = f"http://127.0.0.1:{port}"
    _wait(f"{ui_url}/healthz")
    yield ui_url
    ui_app.state.control_plane.close()
    server.stop()
