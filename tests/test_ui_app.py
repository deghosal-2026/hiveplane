"""Tests for the operator UI application shell (M22, #83)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hiveplane.ui.app import create_ui_app
from hiveplane.ui.client import ControlPlaneError
from ui_fakes import FakeControlPlaneClient


def test_healthz_reports_ok() -> None:
    client = TestClient(create_ui_app(client=FakeControlPlaneClient()))

    assert client.get("/healthz").json() == {"status": "ok"}


def test_default_client_uses_configured_api_url() -> None:
    app = create_ui_app()

    assert app.state.control_plane.base_url == "http://localhost:8000"


def test_control_plane_error_renders_502_page() -> None:
    fake = FakeControlPlaneClient()
    fake.errors["list_workloads"] = ControlPlaneError(503, "control plane down")
    app = create_ui_app(client=fake)

    @app.get("/boom")
    def boom() -> dict[str, Any]:
        app.state.control_plane.list_workloads()
        return {}

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 502
    assert "control plane down" in response.text
