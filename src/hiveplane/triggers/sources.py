"""Concrete trigger sources: GitHub and Alertmanager (M28-01/M28-02, D23).

Each source verifies its provider's signature, matches the trigger's declared
filter, and normalizes the provider payload into the event the task template
renders against. A source never submits runs itself — it returns zero or more
:class:`SourceEvent`s that the API feeds to the trigger engine.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import JsonValue

from hiveplane.triggers.ingest import SignatureError
from hiveplane.triggers.schema import TriggerFilter, TriggerSpec


@dataclass(frozen=True, slots=True)
class SourceEvent:
    """One normalized event extracted from a provider delivery."""

    payload: dict[str, JsonValue] = field(default_factory=dict)
    event_type: str | None = None
    dedup_key: str | None = None


def body_signature(secret: str, body: bytes) -> str:
    """Return the hex HMAC-SHA256 of a raw body (no timestamp/nonce)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _provided_signature(value: str) -> str:
    return value.split("=", 1)[-1] if value else ""


class GitHubSource:
    """Parses GitHub webhooks (PRs, push, labels, ``/hiveplane`` comments)."""

    def parse(
        self,
        spec: TriggerSpec,
        *,
        headers: Mapping[str, str],
        body: bytes,
        secret: str,
    ) -> list[SourceEvent]:
        """Verify ``X-Hub-Signature-256`` and return matching events."""
        provided = _provided_signature(headers.get("X-Hub-Signature-256", ""))
        expected = body_signature(secret, body)
        if not provided or not hmac.compare_digest(expected, provided):
            raise SignatureError("github signature verification failed")
        event_type = headers.get("X-GitHub-Event", "")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("github payload must be an object")
        if not _github_matches(spec.filter, event_type, payload):
            return []
        return [SourceEvent(payload=_normalize(payload), event_type=event_type)]


def _github_matches(
    trigger_filter: TriggerFilter, event_type: str, payload: Mapping[str, JsonValue]
) -> bool:
    repository = payload.get("repository")
    repo = repository.get("full_name") if isinstance(repository, Mapping) else None
    if trigger_filter.repo is not None and repo != trigger_filter.repo:
        return False
    if event_type == "issue_comment":
        comment = payload.get("comment")
        issue = payload.get("issue")
        text = comment.get("body") if isinstance(comment, Mapping) else None
        is_pr = isinstance(issue, Mapping) and bool(issue.get("pull_request"))
        if not is_pr or not isinstance(text, str) or "/hiveplane" not in text:
            return False
        # A command comment re-triggers PR triggers.
        return not trigger_filter.events or bool(
            {"pull_request", "issue_comment"} & set(trigger_filter.events)
        )
    if trigger_filter.events and event_type not in trigger_filter.events:
        return False
    return not (
        trigger_filter.actions
        and event_type in ("pull_request", "issues", "label")
        and payload.get("action") not in trigger_filter.actions
    )


def _normalize(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Lift a PR/issue number to the top level for uniform templating."""
    normalized = dict(payload)
    if "number" in normalized:
        return normalized
    for key in ("pull_request", "issue"):
        nested = payload.get(key)
        if isinstance(nested, Mapping) and "number" in nested:
            normalized["number"] = nested["number"]
            break
    return normalized


class AlertmanagerSource:
    """Parses Prometheus Alertmanager webhooks, one event per alert."""

    def parse(
        self,
        spec: TriggerSpec,
        *,
        headers: Mapping[str, str],
        body: bytes,
        secret: str | None,
    ) -> list[SourceEvent]:
        """Verify the optional body signature and return one event per alert."""
        if secret is not None:
            provided = _provided_signature(headers.get("X-HivePlane-Signature", ""))
            if not provided or not hmac.compare_digest(
                body_signature(secret, body), provided
            ):
                raise SignatureError("alertmanager signature verification failed")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("alertmanager payload must be an object")
        alerts = payload.get("alerts", [])
        if not isinstance(alerts, list):
            raise ValueError("alertmanager payload requires an 'alerts' list")
        events: list[SourceEvent] = []
        for alert in alerts:
            if not isinstance(alert, Mapping):
                continue
            status = alert.get("status")
            if spec.filter.events and status not in spec.filter.events:
                continue
            merged = dict(alert)
            merged["groupStatus"] = payload.get("status")
            fingerprint = alert.get("fingerprint")
            events.append(
                SourceEvent(
                    payload=merged,
                    event_type=str(status) if status is not None else None,
                    dedup_key=str(fingerprint) if fingerprint else None,
                )
            )
        return events
