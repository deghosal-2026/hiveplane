"""L2 — the REST contract the operator UI and CLI consume (M23, #93).

Exercises a representative endpoint matrix against the live stack, each with a
happy path and (where applicable) an error path.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from hiveplane.core.manifest import load_manifest

_API_BASE = os.environ.get("HIVEPLANE_API_URL", "http://localhost:8100").rstrip("/")
_WORKLOADS_DIR = Path(__file__).resolve().parents[2] / "examples" / "workloads"
_MODEL = "openai/gpt-4o/2024-08-06"


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        f"{_API_BASE}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, body


@pytest.fixture(scope="module")
def repo_agent() -> dict[str, Any]:
    """Register repo-agent (idempotent) and return its manifest payload."""
    payload = load_manifest(_WORKLOADS_DIR / "repo-agent.yaml").model_dump(
        by_alias=True, mode="json"
    )
    status, _ = _request("POST", "/workloads", payload)
    assert status in (200, 201), status
    return payload


@pytest.mark.docker
def test_manifest_schema() -> None:
    status, body = _request("GET", "/manifest/schema")

    assert status == 200
    assert body["title"] == "AgentWorkload"


@pytest.mark.docker
def test_health_endpoints() -> None:
    assert _request("GET", "/healthz")[0] == 200
    assert _request("GET", "/readyz")[0] == 200


@pytest.mark.docker
def test_registry_crud_and_errors(repo_agent: dict[str, Any]) -> None:
    assert _request("GET", "/workloads")[0] == 200
    assert _request("GET", "/workloads/repo-agent")[0] == 200
    assert _request("GET", "/workloads/repo-agent/versions")[0] == 200
    assert _request("GET", "/workloads/repo-agent/admission")[0] == 200
    assert _request("GET", "/workloads/repo-agent/attestations")[0] == 200
    assert _request("GET", "/workloads/repo-agent/triggers")[0] == 200

    status, _ = _request("GET", "/workloads/ghost")
    assert status == 404, "unknown workload must 404"


@pytest.mark.docker
def test_unknown_tool_is_rejected() -> None:
    status, _ = _request("POST", "/tools", {"tool_id": "", "trust_level": "read_only"})

    assert status == 422


@pytest.mark.docker
def test_run_lifecycle_and_story(repo_agent: dict[str, Any]) -> None:
    status, run = _request(
        "POST",
        "/runs",
        {
            "workload": "repo-agent",
            "caller": "cli",
            "context": "sandbox",
            "model_identity": _MODEL,
        },
    )
    assert status == 201, run
    run_id = run["id"]

    assert _request("GET", f"/runs/{run_id}")[0] == 200
    assert _request("GET", f"/runs/{run_id}/events")[0] == 200
    assert _request("GET", f"/runs/{run_id}/usage")[0] == 200
    story_status, story = _request("GET", f"/runs/{run_id}/story")
    assert story_status == 200 and story["run_id"] == run_id


@pytest.mark.docker
def test_unknown_run_is_404() -> None:
    assert _request("GET", "/runs/ghost")[0] == 404


@pytest.mark.docker
def test_uncertified_run_is_refused_production(repo_agent: dict[str, Any]) -> None:
    status, _ = _request(
        "POST",
        "/runs",
        {
            "workload": "repo-agent",
            "caller": "cli",
            "context": "production",
            "model_identity": _MODEL,
        },
    )

    assert status == 403, "an uncertified workload must be refused production"


@pytest.mark.docker
def test_policy_approvals_certifications_and_spend_read() -> None:
    assert _request("GET", "/policy-packs")[0] == 200
    assert _request("GET", "/approvals")[0] == 200
    assert _request("GET", "/certifications")[0] == 200
    assert _request("GET", "/spend")[0] == 200
    assert _request("GET", "/tools")[0] == 200
