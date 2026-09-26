"""M33 API + CLI tests for compare and compare-baseline (#231, #233)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hiveplane.api.app import create_app
from hiveplane.certification.models import TargetContext
from hiveplane.certification.runner import ReferenceExecutor
from hiveplane.cli import app
from hiveplane.core.workload import AgentWorkload
from test_certification_m33 import _coordinator, _setup, _write_corpus

runner = CliRunner()

_DIFF = {
    "workload_id": "repo-agent",
    "before_attestation_id": "att-1",
    "after_attestation_id": "att-2",
    "total": 2,
    "passed_before": 2,
    "passed_after": 1,
    "regressed": [
        {
            "task_id": "t2",
            "before": "pass",
            "after": "fail",
            "severity": "critical",
            "critical": True,
            "latency_delta_ms": 49,
            "tokens_delta": 20,
            "cost_delta_usd": 0.02,
        }
    ],
    "improved": [],
    "added": [],
    "removed": [],
    "blocked": True,
    "severity": "critical",
    "critical_regressions": ["t2"],
    "warnings": [],
    "baseline_attestation_id": "att-1",
    "summary": "CRITICAL: 1 regression(s).",
}


def test_cli_compare_json(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (200, json.dumps(_DIFF))
    )
    result = runner.invoke(app, ["certs", "compare", "att-1", "att-2", "--json"])
    assert result.exit_code == 0
    assert '"severity": "critical"' in result.output


def test_cli_compare_human(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (200, json.dumps(_DIFF))
    )
    result = runner.invoke(app, ["certs", "compare", "att-1", "att-2"])
    assert result.exit_code == 0
    assert "BLOCKED" in result.output
    assert "t2" in result.output


def test_cli_compare_baseline(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        return 200, json.dumps(_DIFF)

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(
        app, ["certs", "compare-baseline", "repo-agent", "att-2", "--baseline", "att-1"]
    )
    assert result.exit_code == 0
    assert calls[0].endswith(
        "/certifications/compare-baseline/att-2?workload=repo-agent&baseline=att-1"
    )


def test_cli_compare_failure_exits_nonzero(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (404, "{}")
    )
    result = runner.invoke(app, ["certs", "compare", "a", "b"])
    assert result.exit_code == 1


def test_api_compare_baseline(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    registry, service, store = _setup(make_manifest, corpora_dir)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir)
    first = coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    second = coordinator.certify("repo-agent", target_context=TargetContext.PRODUCTION)

    app = create_app(registry)
    app.state.certification_coordinator = coordinator
    client = TestClient(app)

    response = client.get(
        f"/certifications/compare-baseline/{second.record_id}",
        params={"workload": "repo-agent"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["baseline_attestation_id"] == first.attestation.attestation_id
    assert body["blocked"] is False

    missing = client.get(
        "/certifications/compare-baseline/att-nope", params={"workload": "repo-agent"}
    )
    assert missing.status_code == 404
