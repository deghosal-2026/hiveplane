"""Tests for GitHub and Alertmanager source adapters (M28-01/M28-02)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from hiveplane.triggers.ingest import SignatureError
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.sources import (
    AlertmanagerSource,
    GitHubSource,
    SourceEvent,
    body_signature,
)

_SECRET = "hook-secret"


def _github_spec(**overrides: object) -> TriggerSpec:
    base: dict[str, object] = {
        "id": "pr-analysis",
        "source": "github",
        "target": {"kind": "workload", "ref": "agent-1"},
        "filter": {"event": ["pull_request"], "actions": ["opened", "synchronize"]},
        "task_template": {"pr": "{{ event.number }}"},
    }
    base.update(overrides)
    return TriggerSpec.model_validate(base)


def _headers(event: str, body: bytes, *, secret: str = _SECRET) -> dict[str, str]:
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-GitHub-Event": event, "X-Hub-Signature-256": f"sha256={signature}"}


def _pr_payload(action: str = "opened", *, repo: str = "acme/fleet") -> bytes:
    return json.dumps(
        {
            "action": action,
            "number": 42,
            "repository": {"full_name": repo},
            "pull_request": {"number": 42, "title": "Fix"},
        }
    ).encode()


def _comment_payload(body: str, *, repo: str = "acme/fleet") -> bytes:
    return json.dumps(
        {
            "action": "created",
            "repository": {"full_name": repo},
            "issue": {"number": 42, "pull_request": {"url": "x"}},
            "comment": {"body": body},
        }
    ).encode()


def _gh(spec: TriggerSpec, event: str, body: bytes) -> list[SourceEvent]:
    return GitHubSource().parse(
        spec, headers=_headers(event, body), body=body, secret=_SECRET
    )


def test_github_pr_opened_matches_and_normalizes() -> None:
    body = _pr_payload("opened")
    events = _gh(_github_spec(), "pull_request", body)
    assert len(events) == 1
    assert events[0].payload["number"] == 42
    assert events[0].event_type == "pull_request"


def test_github_bad_signature_is_rejected() -> None:
    body = _pr_payload()
    headers = _headers("pull_request", body)
    headers["X-Hub-Signature-256"] = "sha256=deadbeef"
    with pytest.raises(SignatureError):
        GitHubSource().parse(_github_spec(), headers=headers, body=body, secret=_SECRET)


def test_github_unmatched_action_is_ignored() -> None:
    body = _pr_payload("closed")
    assert _gh(_github_spec(), "pull_request", body) == []


def test_github_repo_mismatch_is_ignored() -> None:
    body = _pr_payload("opened", repo="other/repo")
    spec = _github_spec(filter={"event": ["pull_request"], "repo": "acme/fleet"})
    assert _gh(spec, "pull_request", body) == []


def test_github_hiveplane_comment_re_triggers() -> None:
    body = _comment_payload("/hiveplane re-run")
    events = _gh(_github_spec(), "issue_comment", body)
    assert len(events) == 1
    assert events[0].payload["number"] == 42


def test_github_plain_comment_is_ignored() -> None:
    body = _comment_payload("nice work")
    assert _gh(_github_spec(), "issue_comment", body) == []


def test_github_push_matches_when_declared() -> None:
    body = json.dumps(
        {"ref": "refs/heads/main", "repository": {"full_name": "acme/fleet"}}
    ).encode()
    spec = _github_spec(filter={"event": ["push"]})
    assert len(_gh(spec, "push", body)) == 1


def _alertmanager_spec(**overrides: object) -> TriggerSpec:
    base: dict[str, object] = {
        "id": "alerts",
        "source": "alertmanager",
        "target": {"kind": "workload", "ref": "agent-1"},
        "dedup": {"key": "{{ event.fingerprint }}"},
    }
    base.update(overrides)
    return TriggerSpec.model_validate(base)


def _am_payload(*alerts: dict[str, object], status: str = "firing") -> bytes:
    return json.dumps({"status": status, "alerts": list(alerts)}).encode()


def _alert(fingerprint: str, *, status: str = "firing") -> dict[str, object]:
    return {"status": status, "fingerprint": fingerprint, "labels": {"alertname": "HighCPU"}}


def test_alertmanager_emits_one_event_per_alert_with_fingerprint() -> None:
    body = _am_payload(_alert("fp-1"), _alert("fp-2"))
    events = AlertmanagerSource().parse(_alertmanager_spec(), headers={}, body=body, secret=None)
    assert [event.dedup_key for event in events] == ["fp-1", "fp-2"]
    assert events[0].payload["fingerprint"] == "fp-1"


def test_alertmanager_status_filter() -> None:
    body = _am_payload(_alert("fp-1", status="resolved"))
    spec = _alertmanager_spec(filter={"event": ["firing"]})
    assert AlertmanagerSource().parse(spec, headers={}, body=body, secret=None) == []


def test_alertmanager_body_signature_is_verified() -> None:
    body = _am_payload(_alert("fp-1"))
    signature = body_signature(_SECRET, body)
    events = AlertmanagerSource().parse(
        _alertmanager_spec(),
        headers={"X-HivePlane-Signature": f"sha256={signature}"},
        body=body,
        secret=_SECRET,
    )
    assert len(events) == 1
    with pytest.raises(SignatureError):
        AlertmanagerSource().parse(
            _alertmanager_spec(),
            headers={"X-HivePlane-Signature": "sha256=bad"},
            body=body,
            secret=_SECRET,
        )
