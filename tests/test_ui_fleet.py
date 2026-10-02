"""UI tests for incident mode: big red button and banner (M53)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.tenancy.models import Role
from hiveplane.ui.app import create_ui_app
from hiveplane.ui.session import SESSION_COOKIE
from ui_fakes import FakeControlPlaneClient, session_token


def _app(fake: FakeControlPlaneClient) -> TestClient:
    return TestClient(create_ui_app(client=fake), follow_redirects=False)


def test_admin_sees_halt_button() -> None:
    fake = FakeControlPlaneClient()

    response = _app(fake).get("/")

    assert response.status_code == 200
    assert "Halt fleet" in response.text


def test_viewer_does_not_see_halt_button() -> None:
    fake = FakeControlPlaneClient()
    client = _app(fake)
    client.cookies.set(SESSION_COOKIE, session_token(Role.VIEWER, operator_id="v"))

    response = client.get("/")

    assert response.status_code == 200
    assert "Halt fleet" not in response.text


def test_pause_posts_to_client_and_redirects() -> None:
    fake = FakeControlPlaneClient()
    client = _app(fake)
    client.cookies.set(SESSION_COOKIE, session_token(Role.ADMIN, operator_id="alice"))

    response = client.post("/fleet/pause", data={"reason": "prod down"})

    assert response.status_code == 303
    assert response.headers["location"] == "/?ok=fleet%20halted"
    assert ("pause_fleet", ("alice", "prod down")) in fake.calls


def test_resume_posts_to_client_and_redirects() -> None:
    fake = FakeControlPlaneClient()
    client = _app(fake)
    client.cookies.set(SESSION_COOKIE, session_token(Role.ADMIN, operator_id="bob"))

    response = client.post("/fleet/resume")

    assert response.status_code == 303
    assert response.headers["location"] == "/?ok=fleet%20resumed"
    assert ("resume_fleet", ("bob", None)) in fake.calls


def test_halted_state_renders_banner_and_resume_button() -> None:
    fake = FakeControlPlaneClient()
    fake.fleet_state_data = {
        "halted": True,
        "active": {
            "incident_id": "inc-1",
            "scope": "fleet",
            "actor": "alice",
        },
        "history": [],
    }

    response = _app(fake).get("/")

    assert response.status_code == 200
    assert "HALTED" in response.text
    assert "inc-1" in response.text
    assert "Resume fleet" in response.text


def test_viewer_cannot_post_pause() -> None:
    fake = FakeControlPlaneClient()
    client = _app(fake)
    client.cookies.set(SESSION_COOKIE, session_token(Role.VIEWER, operator_id="v"))

    response = client.post("/fleet/pause")

    assert response.status_code == 303
    assert "not%20permitted" in response.headers["location"]
    assert all(name != "pause_fleet" for name, _ in fake.calls)
