"""L5 — governance decisions in the shipped stack (M23, #93).

Exercises the policy surface deterministically: destructive tools escalate in
production, and uncertified workloads are denied production. Budget enforcement
and output shaping require priced usage / a shaped payload and are covered by
the in-process suites plus the seeded `deploy/testdata/governance/` fixtures.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

_API_BASE = os.environ.get("HIVEPLANE_API_URL", "http://localhost:8100").rstrip("/")
_GOVERNANCE_DIR = (
    Path(__file__).resolve().parents[2] / "deploy" / "testdata" / "governance"
)


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


def _fixture(name: str) -> dict[str, Any]:
    loaded = json.loads((_GOVERNANCE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _policy_context(fixture: dict[str, Any], *, certified: bool) -> dict[str, Any]:
    return {
        "run_id": "governance-probe",
        "workload": fixture["workload"],
        "environment": "production",
        "action_class": fixture["action_class"],
        "tool_id": fixture["tool_id"],
        "tool_trust": "destructive",
        "certification_status": "certified" if certified else "uncertified",
    }


@pytest.mark.docker
def test_destructive_tool_escalates_in_production() -> None:
    fixture = _fixture("destructive-call")

    status, decision = _request(
        "POST", "/policy/evaluate", _policy_context(fixture, certified=True)
    )

    assert status == 200, decision
    assert decision["outcome"] == "escalate", decision
    assert decision["rule"] == "trust.destructive", decision


@pytest.mark.docker
def test_uncertified_workload_is_denied_production() -> None:
    fixture = _fixture("destructive-call")

    status, decision = _request(
        "POST", "/policy/evaluate", _policy_context(fixture, certified=False)
    )

    assert status == 200, decision
    assert decision["outcome"] == "deny", decision
    assert decision["rule"] == "certification.production", decision


@pytest.mark.docker
def test_spend_surface_reports_budgets() -> None:
    status, spend = _request("GET", "/spend")

    assert status == 200, spend
    assert "total_usd" in spend
    assert "by_workload" in spend
