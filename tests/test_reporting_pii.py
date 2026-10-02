"""Tests for PII detection, scrubbing, and audit-chain-safe redaction (M57-06)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.artifacts.backend import LocalBlobBackend, content_address
from hiveplane.artifacts.service import ArtifactService
from hiveplane.artifacts.store import InMemoryArtifactStore
from hiveplane.config import get_settings
from hiveplane.execution.wiring import build_audit_log
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.reporting.models import ScrubResult
from hiveplane.reporting.pii import PII_PATTERNS, PIIScrubber, ScrubbingAuditLog

_EMAIL = "alice@example.com"
_PHONE = "+1 (555) 123-4567"
_SSN = "123-45-6789"
_CARD = "4111 1111 1111 1111"
_IPV4 = "192.168.1.10"


def test_scrub_redacts_email() -> None:
    result = PIIScrubber().scrub(f"reach {_EMAIL} today")
    assert result.text == "reach [REDACTED:email] today"
    assert [(r.kind, r.count) for r in result.redactions] == [("email", 1)]


def test_scrub_redacts_phone() -> None:
    result = PIIScrubber().scrub(f"call {_PHONE} now")
    assert result.text == "call [REDACTED:phone] now"
    assert [(r.kind, r.count) for r in result.redactions] == [("phone", 1)]


@pytest.mark.parametrize(
    "phone",
    [
        "555-123-4567",
        "555.123.4567",
        "555 123 4567",
        "(555) 123-4567",
        "+1 555-123-4567",
        "+1 (555) 123-4567",
        "1-555-123-4567",
    ],
)
def test_scrub_redacts_common_phone_formats(phone: str) -> None:
    result = PIIScrubber().scrub(f"call {phone} now")
    assert result.text == "call [REDACTED:phone] now"
    assert [(r.kind, r.count) for r in result.redactions] == [("phone", 1)]


@pytest.mark.parametrize("value", ["1727000000", "1234567890", "19999999999"])
def test_scrub_does_not_redact_bare_digit_runs(value: str) -> None:
    result = PIIScrubber().scrub(f"timestamp {value} ok")
    assert result.text == f"timestamp {value} ok"
    assert result.redactions == []


def test_scrub_redacts_ssn() -> None:
    result = PIIScrubber().scrub(f"ssn {_SSN} on file")
    assert result.text == "ssn [REDACTED:ssn] on file"
    assert [(r.kind, r.count) for r in result.redactions] == [("ssn", 1)]


def test_scrub_redacts_credit_card() -> None:
    result = PIIScrubber().scrub(f"card {_CARD} charged")
    assert result.text == "card [REDACTED:credit_card] charged"
    assert [(r.kind, r.count) for r in result.redactions] == [("credit_card", 1)]


def test_scrub_leaves_non_luhn_digit_runs() -> None:
    result = PIIScrubber().scrub("order 1234 5678 9012 3456 shipped")
    assert result.text == "order 1234 5678 9012 3456 shipped"
    assert result.redactions == []


def test_scrub_redacts_ipv4() -> None:
    result = PIIScrubber().scrub(f"host {_IPV4} healthy")
    assert result.text == "host [REDACTED:ipv4] healthy"
    assert [(r.kind, r.count) for r in result.redactions] == [("ipv4", 1)]


def test_scrub_leaves_non_pii_untouched() -> None:
    text = "the quick brown fox jumps over 42 lazy dogs"
    result = PIIScrubber().scrub(text)
    assert result.text == text
    assert result.redactions == []


def test_scrub_none_returns_empty_with_no_redactions() -> None:
    result = PIIScrubber().scrub(None)
    assert isinstance(result, ScrubResult)
    assert result.text == ""
    assert result.redactions == []


def test_scrub_counts_redactions_by_kind() -> None:
    text = f"{_EMAIL} and bob@example.org ping {_IPV4}"
    result = PIIScrubber().scrub(text)
    assert result.text == "[REDACTED:email] and [REDACTED:email] ping [REDACTED:ipv4]"
    assert [(r.kind, r.count) for r in result.redactions] == [
        ("email", 2),
        ("ipv4", 1),
    ]


def test_scrub_disabled_is_a_passthrough() -> None:
    scrubber = PIIScrubber(enabled=False)
    result = scrubber.scrub(f"reach {_EMAIL}")
    assert result.text == f"reach {_EMAIL}"
    assert result.redactions == []


def test_scrub_supports_custom_patterns() -> None:
    scrubber = PIIScrubber(patterns=[("token", re.compile(r"tok-\d+"))])
    result = scrubber.scrub(f"use tok-123 and {_EMAIL}")
    assert result.text == f"use [REDACTED:token] and {_EMAIL}"
    assert [(r.kind, r.count) for r in result.redactions] == [("token", 1)]


def test_scrub_accepts_mapping_patterns() -> None:
    scrubber = PIIScrubber(patterns={"token": re.compile(r"tok-\d+")})
    assert scrubber.scrub("use tok-9").text == "use [REDACTED:token]"


def test_pii_patterns_expose_known_kinds() -> None:
    assert {"email", "phone", "ssn", "credit_card", "ipv4"} <= set(PII_PATTERNS)
    assert all(isinstance(pattern, re.Pattern) for pattern in PII_PATTERNS.values())


def test_scrub_hash_is_salted_sha256() -> None:
    scrubber = PIIScrubber(salt="pepper")
    digest = scrubber.scrub_hash(_EMAIL)
    assert len(digest) == 64
    assert digest == scrubber.scrub_hash(_EMAIL)
    assert PIIScrubber(salt="other").scrub_hash(_EMAIL) != digest
    assert not digest.startswith(_EMAIL)


def test_scrubbing_audit_log_redacts_detail_before_hashing() -> None:
    audit = InMemoryAuditLog()
    log = ScrubbingAuditLog(audit, PIIScrubber())

    record = log.append("alice", "contact.created", "c-1", detail=f"email {_EMAIL}")

    assert record.detail == "email [REDACTED:email]"
    assert log.verify() is True
    stored = log.records()[0]
    assert stored.detail == "email [REDACTED:email]"
    assert _EMAIL not in (stored.detail or "")


def test_scrubbing_audit_log_keeps_chain_verifiable_across_appends() -> None:
    audit = InMemoryAuditLog()
    log = ScrubbingAuditLog(audit, PIIScrubber())

    log.append("alice", "a", "s", detail=f"ip {_IPV4}")
    log.append("bob", "b", "s", detail=f"ssn {_SSN}")
    log.append("carol", "c", "s", detail="clean detail")

    assert log.verify() is True
    details = [record.detail for record in log.records()]
    assert details == ["ip [REDACTED:ipv4]", "ssn [REDACTED:ssn]", "clean detail"]


def test_scrubbing_audit_log_never_persists_raw_pii() -> None:
    audit = InMemoryAuditLog()
    log = ScrubbingAuditLog(audit, PIIScrubber())

    log.append("alice", "leak", "s", detail=f"{_EMAIL} card {_CARD}")

    raw = "".join(record.detail or "" for record in log.records())
    assert _EMAIL not in raw
    assert _CARD not in raw
    assert log.verify() is True


def test_scrubbing_audit_log_delegates_none_detail_and_lifecycle() -> None:
    audit = InMemoryAuditLog(clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    log = ScrubbingAuditLog(audit, PIIScrubber())

    record = log.append("alice", "noop", "s")
    assert record.detail is None
    assert log.anchor() == "0" * 64
    assert log.prune(before=datetime(2027, 1, 1, tzinfo=UTC)) == 1
    assert log.records() == []
    assert log.verify() is True


def test_build_audit_log_wraps_backend_when_pii_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_REPORTING__PII_ENABLED", "true")
    get_settings.cache_clear()

    log = build_audit_log()

    assert isinstance(log, ScrubbingAuditLog)


def test_build_audit_log_is_plain_when_pii_disabled() -> None:
    log = build_audit_log()
    assert isinstance(log, InMemoryAuditLog)


def test_artifact_capture_scrubs_text_when_scrubber_bound(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    service = ArtifactService(
        store,
        LocalBlobBackend(tmp_path),
        scrubber=PIIScrubber(),
        artifact_id_factory=lambda: "art-1",
    )

    artifact = service.capture(
        tenant_id="default",
        run_id="run-1",
        filename="log.txt",
        data=f"user {_EMAIL}".encode(),
    )

    _, data = service.data("art-1", tenant_id="default")
    assert data == b"user [REDACTED:email]"
    assert artifact.content_hash == content_address(b"user [REDACTED:email]")


def test_artifact_capture_leaves_binary_untouched(tmp_path: Path) -> None:
    service = ArtifactService(
        InMemoryArtifactStore(),
        LocalBlobBackend(tmp_path),
        scrubber=PIIScrubber(),
        artifact_id_factory=lambda: "art-1",
    )
    payload = b"\x00\x01\x02\xff user " + _EMAIL.encode()

    artifact = service.capture(
        tenant_id="default", run_id="run-1", filename="blob.bin", data=payload
    )

    _, data = service.data("art-1", tenant_id="default")
    assert data == payload
    assert artifact.content_hash == content_address(payload)


def test_pii_scrub_endpoint_redacts_text() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/pii/scrub",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"text": f"contact {_EMAIL} at {_IPV4}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "contact [REDACTED:email] at [REDACTED:ipv4]"
    assert body["redactions"] == [
        {"kind": "email", "count": 1},
        {"kind": "ipv4", "count": 1},
    ]
