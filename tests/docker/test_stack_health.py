"""L1 — the running stack is healthy (M23, #93).

Runs against the compose stack the runner brought up. Asserts the control plane
is actually ready (honest `/readyz`, #130) and that the observability stack
answers its health endpoints.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

_API_BASE = os.environ.get("HIVEPLANE_API_URL", "http://localhost:8100").rstrip("/")
_GRAFANA_BASE = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
_PROMETHEUS_BASE = os.environ.get("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
_TEMPO_BASE = os.environ.get("TEMPO_URL", "http://localhost:3200").rstrip("/")


def _get(url: str, timeout: float = 10.0) -> tuple[int, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, body
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


@pytest.mark.docker
def test_healthz_is_ok() -> None:
    status, body = _get(f"{_API_BASE}/healthz")

    assert status == 200, body
    assert body == {"status": "ok"}


@pytest.mark.docker
def test_readyz_reports_ready() -> None:
    status, body = _get(f"{_API_BASE}/readyz")

    assert status == 200, body
    assert body.get("status") == "ready", body
    assert "reasons" not in body


@pytest.mark.docker
def test_observability_services_are_healthy() -> None:
    checks = {
        "grafana": f"{_GRAFANA_BASE}/api/health",
        "prometheus": f"{_PROMETHEUS_BASE}/-/healthy",
        "tempo": f"{_TEMPO_BASE}/ready",
    }

    deadline = time.monotonic() + 90.0
    pending = dict(checks)
    bodies: dict[str, Any] = {}
    while pending and time.monotonic() < deadline:
        for name, url in list(pending.items()):
            status, body = _get(url)
            if status == 200:
                pending.pop(name, None)
            else:
                bodies[name] = (status, body)
        if pending:
            time.sleep(3)

    assert not pending, f"services not healthy within timeout: {bodies}"
