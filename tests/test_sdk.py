"""SDK round-trip against a live control plane (M56-02)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
import uvicorn
from fastapi import FastAPI

from hiveplane.api.app import create_app
from hiveplane.core.workload import AgentWorkload
from hiveplane.sdk import HivePlaneClient


@contextmanager
def _live_server(app: FastAPI) -> Iterator[str]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(1000):
        if server.started:
            break
        time.sleep(0.01)
    else:  # pragma: no cover - startup failure
        raise RuntimeError("uvicorn server did not start")
    sockets = server.servers[0].sockets
    port = sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_sdk_submits_and_retrieves_a_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    app.state.registry_service.create(
        make_manifest(
            name="agent-1",
            sandbox={
                "enabled": True,
                "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            },
        )
    )
    with _live_server(app) as base_url, HivePlaneClient(base_url) as client:
        run = client.submit_run("agent-1", context="sandbox", task={"x": 1})
        assert run["workload_id"] == "agent-1"

        fetched = client.get_run(run["id"])
        assert fetched["id"] == run["id"]

        page = client.list_runs(limit=1)
        assert len(page["items"]) == 1
        assert page["count"] >= 1

        assert client.list_health() is not None
        assert client.list_workloads()[0]["name"] == "agent-1"


def test_sdk_wait_for_run_times_out_on_a_queued_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    app.state.registry_service.create(
        make_manifest(
            name="agent-1",
            sandbox={
                "enabled": True,
                "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            },
        )
    )
    with _live_server(app) as base_url, HivePlaneClient(base_url) as client:
        run = client.submit_run("agent-1", context="sandbox")

        with pytest.raises(TimeoutError):
            client.wait_for_run(run["id"], timeout=0.2, interval=0.05)
