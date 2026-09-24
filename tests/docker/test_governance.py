"""L5 — governance decisions in the shipped stack (M23, #93).

The policy engine's tool-level decisions (destructive escalation, certification
denial) happen inside the run/admission pipeline, not via the standalone
``/policy/evaluate`` endpoint (which lacks the workload's tool spec). So L5
exercises the real admission gate: an uncertified destructive workload is
refused production but admitted to sandbox, and the spend surface reports the
fleet's budget posture.
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
def incident_agent() -> None:
    """Register the destructive incident-agent workload (idempotent)."""
    payload = load_manifest(_WORKLOADS_DIR / "incident-agent.yaml").model_dump(
        by_alias=True, mode="json"
    )
    assert _request("POST", "/workloads", payload)[0] in (200, 201, 409)


@pytest.mark.docker
def test_uncertified_destructive_workload_refused_production(
    incident_agent: None,
) -> None:
    status, _ = _request(
        "POST",
        "/runs",
        {
            "workload": "incident-agent",
            "caller": "field-test",
            "context": "production",
            "model_identity": "openai/gpt-4o/2024-08-06",
        },
    )

    assert status == 403, "an uncertified workload must be refused production"


@pytest.mark.docker
def test_uncertified_destructive_workload_admitted_to_sandbox(
    incident_agent: None,
) -> None:
    status, run = _request(
        "POST",
        "/runs",
        {
            "workload": "incident-agent",
            "caller": "field-test",
            "context": "sandbox",
            "model_identity": "openai/gpt-4o/2024-08-06",
        },
    )

    assert status == 201, "sandbox context must admit even uncertified workloads"
    assert run["sandbox"] is True


@pytest.mark.docker
def test_spend_surface_reports_budgets() -> None:
    status, spend = _request("GET", "/spend")

    assert status == 200, spend
    assert "by_workload" in spend
    assert "by_team" in spend
