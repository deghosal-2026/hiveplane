"""Tests for the trigger API: CRUD and webhook ingest (M27)."""

from __future__ import annotations

import time
from typing import Any, cast

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.manifest import parse_manifest
from hiveplane.triggers.ingest import sign_webhook

_SECRET = "hook-secret"

_TRIGGER = {
    "id": "t1",
    "source": "webhook",
    "target": {"kind": "workload", "ref": "agent-1"},
    "task_template": {"pr": "{{ event.number }}"},
    "admission_rule": "staging-auto",
}


def _client() -> TestClient:
    app = create_app()
    app.state.trigger_secrets["t1"] = _SECRET
    return TestClient(app)


def _register_staging_workload(client: TestClient) -> None:
    app = cast(Any, client.app)
    app.state.registry_service.create(
        parse_manifest(
            {
                "apiVersion": "hiveplane/v1",
                "kind": "AgentWorkload",
                "metadata": {"name": "agent-1", "owner": "platform-team"},
                "spec": {
                    "runtime": {
                        "adapter": "raw-worker",
                        "entrypoint": "examples.worker:run",
                    },
                    "model": {
                        "strategy": "tiered",
                        "identity": {
                            "provider": "openai",
                            "family": "gpt-4o",
                            "version": "2024-08-06",
                        },
                    },
                    "certification": {
                        "benchmark_corpus": "corpora/x/v1",
                        "staging_threshold": 0.8,
                        "production_threshold": 0.9,
                        "status": "provisional",
                    },
                    "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0},
                },
            }
        )
    )


def _webhook_headers(body: bytes, *, nonce: str = "n1") -> dict[str, str]:
    timestamp = int(time.time())
    signature = sign_webhook(_SECRET, timestamp, nonce, body)
    return {
        "X-HivePlane-Signature": f"sha256={signature}",
        "X-HivePlane-Timestamp": str(timestamp),
        "X-HivePlane-Nonce": nonce,
        "Content-Type": "application/json",
    }


def test_trigger_crud() -> None:
    client = _client()
    assert client.post("/triggers", json=_TRIGGER).status_code == 201
    assert client.get("/triggers").json()[0]["id"] == "t1"
    assert client.get("/triggers/t1").json()["source"] == "webhook"
    assert client.get("/triggers/missing").status_code == 404

    assert client.post("/triggers/t1/disable").json()["enabled"] is False
    assert client.get("/triggers", params={"enabled": False}).json()[0]["id"] == "t1"
    assert client.post("/triggers/t1/enable").json()["enabled"] is True

    assert client.delete("/triggers/t1").status_code == 204
    assert client.get("/triggers/t1").status_code == 404


def test_webhook_with_valid_signature_is_accepted() -> None:
    client = _client()
    _register_staging_workload(client)
    client.post("/triggers", json=_TRIGGER)
    body = b'{"number": 7}'
    response = client.post(
        "/triggers/webhook/t1", content=body, headers=_webhook_headers(body)
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["run_id"]
    assert client.get("/triggers/t1/events").json()[0]["outcome"] == "accepted"
    assert client.get("/triggers/t1/runs").json()[0]["status"] == "submitted"


def test_webhook_with_bad_signature_is_rejected() -> None:
    client = _client()
    client.post("/triggers", json=_TRIGGER)
    headers = _webhook_headers(b"{}")
    headers["X-HivePlane-Signature"] = "sha256=deadbeef"
    response = client.post("/triggers/webhook/t1", content=b"{}", headers=headers)
    assert response.status_code == 401


def test_webhook_replay_is_rejected() -> None:
    client = _client()
    _register_staging_workload(client)
    client.post("/triggers", json=_TRIGGER)
    body = b'{"number": 7}'
    headers = _webhook_headers(body, nonce="nonce-1")
    assert client.post("/triggers/webhook/t1", content=body, headers=headers).status_code == 202
    assert client.post("/triggers/webhook/t1", content=body, headers=headers).status_code == 409


def test_webhook_duplicate_dedup_key_is_conflict() -> None:
    client = _client()
    _register_staging_workload(client)
    client.post("/triggers", json={**_TRIGGER, "dedup": {"key": "{{ event.number }}"}})
    body = b'{"number": 7}'
    first = client.post(
        "/triggers/webhook/t1", content=body, headers=_webhook_headers(body, nonce="a")
    )
    second = client.post(
        "/triggers/webhook/t1", content=body, headers=_webhook_headers(body, nonce="b")
    )
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["outcome"] == "deduplicated"


def test_webhook_without_configured_secret_is_forbidden() -> None:
    app = create_app()
    client = TestClient(app)
    client.post("/triggers", json=_TRIGGER)
    response = client.post(
        "/triggers/webhook/t1", content=b"{}", headers=_webhook_headers(b"{}")
    )
    assert response.status_code == 403


def test_webhook_unknown_trigger_is_not_found() -> None:
    client = _client()
    response = client.post(
        "/triggers/webhook/missing", content=b"{}", headers=_webhook_headers(b"{}")
    )
    assert response.status_code == 404


def _github_headers(body: bytes, event: str, secret: str = _SECRET) -> dict[str, str]:
    import hashlib
    import hmac

    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json",
    }


def test_github_pr_event_starts_a_run_and_comment_retriggers() -> None:
    client = _client()
    _register_staging_workload(client)
    client.post(
        "/triggers",
        json={
            "id": "t1",
            "source": "github",
            "target": {"kind": "workload", "ref": "agent-1"},
            "filter": {"event": ["pull_request"], "actions": ["opened", "synchronize"]},
            "task_template": {"pr": "{{ event.number }}"},
            "admission_rule": "staging-auto",
        },
    )
    pr_body = b'{"action": "opened", "number": 5, "repository": {"full_name": "acme/fleet"}}'
    first = client.post(
        "/triggers/github/t1", content=pr_body, headers=_github_headers(pr_body, "pull_request")
    )
    assert first.status_code == 200
    assert first.json()[0]["accepted"] is True

    comment = (
        b'{"action": "created", "repository": {"full_name": "acme/fleet"}, '
        b'"issue": {"number": 5, "pull_request": {"url": "x"}}, '
        b'"comment": {"body": "/hiveplane again"}}'
    )
    second = client.post(
        "/triggers/github/t1", content=comment, headers=_github_headers(comment, "issue_comment")
    )
    assert second.status_code == 200
    assert second.json()[0]["accepted"] is True


def test_github_bad_signature_is_unauthorized() -> None:
    client = _client()
    client.post(
        "/triggers",
        json={
            "id": "t1",
            "source": "github",
            "target": {"kind": "workload", "ref": "agent-1"},
        },
    )
    headers = _github_headers(b"{}", "pull_request")
    headers["X-Hub-Signature-256"] = "sha256=bad"
    assert client.post("/triggers/github/t1", content=b"{}", headers=headers).status_code == 401


def test_alertmanager_fingerprint_dedup() -> None:
    client = _client()
    _register_staging_workload(client)
    client.post(
        "/triggers",
        json={
            "id": "t1",
            "source": "alertmanager",
            "target": {"kind": "workload", "ref": "agent-1"},
            "dedup": {"key": "{{ event.fingerprint }}"},
            "admission_rule": "staging-auto",
        },
    )
    body = (
        b'{"status": "firing", "alerts": [{"status": "firing", "fingerprint": "fp-1", '
        b'"labels": {"alertname": "HighCPU"}}]}'
    )
    from hiveplane.triggers.sources import body_signature

    headers = {"X-HivePlane-Signature": f"sha256={body_signature(_SECRET, body)}"}
    first = client.post("/triggers/alertmanager/t1", content=body, headers=headers)
    assert first.status_code == 200 and first.json()[0]["accepted"] is True
    second = client.post("/triggers/alertmanager/t1", content=body, headers=headers)
    assert second.json()[0]["outcome"] == "deduplicated"


def test_freeze_suppresses_webhook_and_can_be_lifted() -> None:
    client = _client()
    _register_staging_workload(client)
    client.post("/triggers", json=_TRIGGER)

    freeze = {
        "freeze_id": "fz-1",
        "scope": "workload",
        "scope_ref": "agent-1",
        "starts_at": "2026-01-01T00:00:00Z",
        "ends_at": "2999-01-01T00:00:00Z",
        "declared_by": "operator",
    }
    assert client.post("/triggers/freezes", json=freeze).status_code == 201
    assert client.get("/triggers/freezes").json()[0]["freeze_id"] == "fz-1"

    body = b'{"number": 1}'
    response = client.post(
        "/triggers/webhook/t1", content=body, headers=_webhook_headers(body)
    )
    assert response.status_code == 202
    assert response.json()["outcome"] == "suppressed_freeze"

    assert client.delete("/triggers/freezes/fz-1").status_code == 204
    assert client.get("/triggers/freezes").json() == []


def test_dlq_replay_missing_entry_is_not_found() -> None:
    client = _client()
    assert client.post("/triggers/dlq/missing/replay").status_code == 404


def test_webhook_to_missing_workload_is_dead_lettered() -> None:
    client = _client()
    client.post("/triggers", json={**_TRIGGER, "target": {"kind": "workload", "ref": "nope"}})
    body = b'{"number": 1}'
    response = client.post(
        "/triggers/webhook/t1", content=body, headers=_webhook_headers(body)
    )
    assert response.status_code == 500
    dlq = client.get("/triggers/dlq").json()
    assert dlq and dlq[0]["trigger_id"] == "t1"
