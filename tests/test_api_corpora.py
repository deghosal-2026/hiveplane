"""API tests for corpus publishing (M55-03)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.corpus.templates import TemplateKind, corpus_template


def _document(kind: TemplateKind = TemplateKind.TRIAGE) -> dict[str, Any]:
    return corpus_template(kind, corpus_id="triage").model_dump(
        mode="json", by_alias=True
    )


def test_publish_list_and_get_corpus() -> None:
    client = TestClient(create_app())

    published = client.post("/corpora", json={"corpus": _document()})
    assert published.status_code == 200
    release = published.json()
    assert release["corpus_id"] == "triage"
    assert release["content_hash"].startswith("sha256:")

    listed = client.get("/corpora")
    assert listed.status_code == 200
    assert [r["corpus_id"] for r in listed.json()] == ["triage"]

    fetched = client.get("/corpora/triage/1")
    assert fetched.status_code == 200
    assert fetched.json()["version"] == 1


def test_republish_with_changed_content_conflicts() -> None:
    client = TestClient(create_app())
    document = _document()
    client.post("/corpora", json={"corpus": document})
    document["tasks"] = document["tasks"][:1]

    response = client.post("/corpora", json={"corpus": document})

    assert response.status_code == 409


def test_publish_rejects_malformed_corpus() -> None:
    client = TestClient(create_app())

    response = client.post("/corpora", json={"corpus": {"id": "x", "tasks": []}})

    assert response.status_code == 422


def test_get_missing_corpus_is_404() -> None:
    client = TestClient(create_app())

    assert client.get("/corpora/ghost/9").status_code == 404
