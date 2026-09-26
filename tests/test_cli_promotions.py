"""Tests for the `hiveplane promote` CLI command (M32-06)."""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()

_PROMOTED = {
    "promotion_id": "promo-1",
    "workload": "repo-agent",
    "manifest_version": 1,
    "to_context": "production",
    "status": "promoted",
    "refusal_reason": None,
    "changed_bindings": [],
    "certification_id": "att-1",
}
_REFUSED = {
    "promotion_id": "promo-2",
    "workload": "repo-agent",
    "manifest_version": 2,
    "to_context": "production",
    "status": "refused",
    "refusal_reason": "no valid production certification",
    "changed_bindings": ["model_binding: a -> b"],
    "certification_id": None,
}


def test_promote_admits(monkeypatch: Any) -> None:
    calls: list[tuple[str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((url, payload))
        return 200, json.dumps(_PROMOTED)

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["promote", "repo-agent", "--version", "1"])
    assert result.exit_code == 0
    assert calls[0][0].endswith("/promotions")
    assert "promoted" in result.output


def test_promote_refused_exits_nonzero(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (409, json.dumps({"detail": "refused"})),
    )
    result = runner.invoke(app, ["promote", "repo-agent", "--version", "2"])
    assert result.exit_code == 1
    assert "refused" in result.output


def test_promote_recertify(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        return 200, json.dumps({"promotion": _PROMOTED, "certification_id": "att-1"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["promote", "repo-agent", "--recertify"])
    assert result.exit_code == 0
    assert calls[0].endswith("/promotions/recertify")
    assert "promoted" in result.output


def test_promote_recertify_refused(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps({"promotion": _REFUSED, "certification_id": "att-1"}),
        ),
    )
    result = runner.invoke(app, ["promote", "repo-agent", "--recertify"])
    assert result.exit_code == 1
    assert "refused" in result.output
