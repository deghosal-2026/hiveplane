"""L4 — the certified control loop, end to end (M23, #93).

register → certify (real local model) → admitted production run → terminal
state → recorded story with the model call and fan-out delivery.

Uses the `local_llm` fixture so a missing model is a failure, not a skip.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from hiveplane.core.manifest import load_manifest

_API_BASE = os.environ.get("HIVEPLANE_API_URL", "http://localhost:8100").rstrip("/")
_WORKLOADS_DIR = Path(__file__).resolve().parents[2] / "examples" / "workloads"
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"
_TERMINAL = {"completed", "failed", "cancelled"}


def _canonical_identity() -> str:
    if _ENV_FILE.is_file():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("HIVEPLANE_MODEL__DEFAULT_MODEL="):
                return line.partition("=")[2].strip()
    return "openai/gpt-4o/2024-08-06"


def _request(
    method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 300.0
) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        f"{_API_BASE}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, body


def _poll_run(run_id: str, timeout: float = 300.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, run = _request("GET", f"/runs/{run_id}")
        assert status == 200, run
        if run.get("state") in _TERMINAL:
            return run
        time.sleep(1)
    raise AssertionError(f"run {run_id} never reached a terminal state: {run}")


@pytest.mark.docker
def test_register_certify_run_and_deliver(local_llm: Any) -> None:
    identity = _canonical_identity()
    payload = load_manifest(_WORKLOADS_DIR / "repo-agent.yaml").model_dump(
        by_alias=True, mode="json"
    )
    assert _request("POST", "/workloads", payload)[0] in (200, 201)

    status, record = _request(
        "POST",
        "/certifications",
        {
            "workload": "repo-agent",
            "target_context": "production",
            "model_identity": identity,
        },
    )
    assert status == 201, record
    assert record["certification"]["status"] == "certified", record

    status, run = _request(
        "POST",
        "/runs",
        {
            "workload": "repo-agent",
            "caller": "field-test",
            "context": "production",
            "model_identity": identity,
        },
    )
    assert status == 201, run

    started = _request("POST", f"/runs/{run['id']}/start")
    assert started[0] == 200, started

    finished = _poll_run(run["id"])
    assert finished["state"] == "completed", finished

    story_status, story = _request("GET", f"/runs/{run['id']}/story")
    assert story_status == 200
    kinds = {entry["kind"] for entry in story["entries"]}
    assert "admission" in kinds, story
    assert "model_call" in kinds, "the run story must record the real model call"
    assert "delivery" in kinds, "result fan-out must be recorded"
