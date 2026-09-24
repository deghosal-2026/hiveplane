"""L6 — durability: a paused run survives a control-plane restart (M23, #93).

The docs-agent graph pauses deterministically at its review-gate interrupt. The
test pauses there, restarts the api container, and resumes — proving startup
recovery (#111) and the durable checkpointer (#122) work in the shipped stack.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from hiveplane.core.manifest import load_manifest

_API_BASE = os.environ.get("HIVEPLANE_API_URL", "http://localhost:8100").rstrip("/")
_ROOT = Path(__file__).resolve().parents[2]
_WORKLOADS_DIR = _ROOT / "examples" / "workloads"


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        f"{_API_BASE}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, body


def _wait_for_state(run_id: str, states: set[str], timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, run = _request("GET", f"/runs/{run_id}")
        assert status == 200, run
        if run.get("state") in states:
            return run
        time.sleep(1)
    raise AssertionError(f"run {run_id} never reached {states}: {run}")


def _restart_api() -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(_ROOT),
            "--env-file",
            str(_ROOT / ".env.local"),
            "--profile",
            "local",
            "--profile",
            "test",
            "restart",
            "api",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _wait_ready(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{_API_BASE}/readyz", timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    raise AssertionError("control plane did not become ready after restart")


@pytest.mark.docker
def test_paused_run_survives_restart_and_resumes() -> None:
    payload = load_manifest(_WORKLOADS_DIR / "docs-agent.yaml").model_dump(
        by_alias=True, mode="json"
    )
    assert _request("POST", "/workloads", payload)[0] in (200, 201)

    status, run = _request(
        "POST",
        "/runs",
        {"workload": "docs-agent", "caller": "field-test", "context": "sandbox"},
    )
    assert status == 201, run
    run_id = run["id"]
    assert _request("POST", f"/runs/{run_id}/start")[0] == 200

    paused = _wait_for_state(run_id, {"paused"})
    assert paused["state"] == "paused"

    _restart_api()
    _wait_ready()

    after = _request("GET", f"/runs/{run_id}")[1]
    assert after["state"] == "paused", "a paused run must survive the restart"

    events = _request("GET", f"/runs/{run_id}/events")[1]
    assert any(
        event["type"] == "recovery" for event in events
    ), "startup recovery must record a recovery event"

    assert _request("POST", f"/runs/{run_id}/resume")[0] == 200
    finished = _wait_for_state(run_id, {"completed", "failed", "cancelled"})
    assert finished["state"] == "completed", finished
