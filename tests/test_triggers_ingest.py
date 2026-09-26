"""Webhook ingest tests: HMAC verification and replay protection (M27-02)."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest

from hiveplane.triggers.ingest import (
    IngestError,
    ReplayError,
    SignatureError,
    TimestampError,
    WebhookRequest,
    WebhookVerifier,
    sign_webhook,
)
from hiveplane.triggers.store import InMemoryTriggerStore

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_SECRET = "s3cret"


def _request(
    body: bytes = b'{"number": 1}',
    *,
    timestamp: int | None = None,
    nonce: str = "n1",
) -> WebhookRequest:
    ts = timestamp if timestamp is not None else int(_NOW.timestamp())
    signature = sign_webhook(_SECRET, ts, nonce, body)
    return WebhookRequest(
        signature=f"sha256={signature}",
        timestamp=str(ts),
        nonce=nonce,
        body=body,
    )


def _verifier() -> WebhookVerifier:
    return WebhookVerifier(store=InMemoryTriggerStore(), clock=lambda: _NOW)


def test_valid_signature_is_accepted() -> None:
    _verifier().verify("pr-analysis", _request(), _SECRET)


def test_bad_signature_is_rejected() -> None:
    verifier = _verifier()
    request = _request()
    tampered = WebhookRequest(
        signature=request.signature, timestamp=request.timestamp, nonce=request.nonce,
        body=b'{"number": 2}',
    )
    with pytest.raises(SignatureError):
        verifier.verify("pr-analysis", tampered, _SECRET)


def test_missing_signature_is_rejected() -> None:
    verifier = _verifier()
    request = _request()
    with pytest.raises(SignatureError):
        verifier.verify(
            "pr-analysis",
            WebhookRequest("", request.timestamp, request.nonce, request.body),
            _SECRET,
        )


def test_wrong_secret_is_rejected() -> None:
    with pytest.raises(SignatureError):
        _verifier().verify("pr-analysis", _request(), "wrong")


def test_stale_timestamp_is_rejected() -> None:
    stale = _request(timestamp=int((_NOW - timedelta(seconds=301)).timestamp()))
    with pytest.raises(TimestampError):
        _verifier().verify("pr-analysis", stale, _SECRET)


def test_replayed_nonce_is_rejected() -> None:
    verifier = _verifier()
    request = _request()
    verifier.verify("pr-analysis", request, _SECRET)
    with pytest.raises(ReplayError):
        verifier.verify("pr-analysis", request, _SECRET)


def test_nonce_reusable_after_replay_window() -> None:
    now = {"value": _NOW}
    verifier = WebhookVerifier(
        store=InMemoryTriggerStore(), clock=lambda: now["value"], replay_window_seconds=300
    )
    request = _request()
    verifier.verify("pr-analysis", request, _SECRET)
    now["value"] = _NOW + timedelta(seconds=301)
    replay = _request(timestamp=int(now["value"].timestamp()))
    verifier.verify("pr-analysis", replay, _SECRET)


def test_same_nonce_on_different_triggers_is_independent() -> None:
    verifier = _verifier()
    verifier.verify("trigger-a", _request(), _SECRET)
    verifier.verify("trigger-b", _request(), _SECRET)


def test_ingest_errors_carry_status_codes() -> None:
    assert isinstance(SignatureError("x"), IngestError)
    assert SignatureError("x").status_code == 401
    assert TimestampError("x").status_code == 401
    assert ReplayError("x").status_code == 409


def test_sign_webhook_matches_documented_construction() -> None:
    body = b"payload"
    expected = hmac.new(
        _SECRET.encode(), b"123.n1.payload", hashlib.sha256
    ).hexdigest()
    assert sign_webhook(_SECRET, 123, "n1", body) == expected
