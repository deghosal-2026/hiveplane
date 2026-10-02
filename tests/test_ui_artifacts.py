"""UI test: run detail lists linked artifacts (M54-04)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.ui.app import create_ui_app
from ui_fakes import FakeControlPlaneClient


def test_run_detail_lists_linked_artifacts() -> None:
    fake = FakeControlPlaneClient()
    fake.story = {
        "run_id": "r1",
        "workload": "agent-a",
        "state": "completed",
        "entries": [],
    }
    fake.artifacts = [
        {
            "artifact_id": "art-1",
            "run_id": "r1",
            "size_bytes": 5,
            "content_hash": "sha256:abc",
            "location": "file:///tmp/art-1",
        }
    ]
    client = TestClient(create_ui_app(client=fake))

    response = client.get("/runs/r1")

    assert response.status_code == 200
    assert "art-1" in response.text
    assert "sha256:abc" in response.text
