"""API tests for the `ask` copilot (M53)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.ask.workload import ask_manifest
from hiveplane.certification.models import CertificationStatus
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.models import AdmissionDecision
from hiveplane.tenancy.context import DEFAULT_CONTEXT


def test_ask_answers_fleet_status() -> None:
    client = TestClient(create_app())

    response = client.post("/ask", json={"question": "what is running right now?"})

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "fleet_status"
    assert body["read_only"] is True


def test_ask_refuses_mutation_without_confirmation() -> None:
    client = TestClient(create_app())

    response = client.post("/ask", json={"question": "stop the fleet"})

    assert response.status_code == 200
    body = response.json()
    assert body["requires_confirmation"] is True
    assert body["read_only"] is True


def test_ask_requires_a_question() -> None:
    client = TestClient(create_app())

    response = client.post("/ask", json={"question": ""})

    assert response.status_code == 422


def test_ask_requires_certification() -> None:
    app = create_app()
    app.state.registry_service.create(ask_manifest())
    client = TestClient(app)

    response = client.post("/ask", json={"question": "what is running now?"})

    assert response.status_code == 403


def test_ask_enforces_workload_budget(
    monkeypatch: pytest.MonkeyPatch, make_manifest: Callable[..., AgentWorkload]
) -> None:
    app = create_app()
    app.state.registry_service.create(make_manifest(name="ask"))
    gate = app.state.run_service._admission._certification
    monkeypatch.setattr(
        gate,
        "check_admission",
        lambda name, context, *, ctx: AdmissionDecision(
            workload=name,
            context=context,
            admitted=True,
            actual_status=CertificationStatus.CERTIFIED,
        ),
    )
    today = datetime.now(UTC).date().isoformat()
    app.state.budget_store.add_day_spend(
        "ask", today, 10.0, ctx=DEFAULT_CONTEXT
    )
    client = TestClient(app)

    response = client.post("/ask", json={"question": "what is running now?"})

    assert response.status_code == 403
    assert "budget" in response.json()["detail"].lower()
