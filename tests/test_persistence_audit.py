"""Tests for the tamper-evident audit chain (no database required)."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.persistence.audit import AuditChain, InMemoryAuditLog

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_append_chains_hashes() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    first = log.append("operator", "pause", "run-1")
    second = log.append("operator", "resume", "run-1")
    assert first.prev_hash == "0" * 64
    assert second.prev_hash == first.hash
    assert second.hash != first.hash


def test_verify_detects_tampering() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    log.append("operator", "pause", "run-1")
    log.append("operator", "stop", "run-1")
    assert AuditChain.verify(log.records()) is None
    assert log.verify() is True

    records = log.records()
    records[0] = records[0].model_copy(update={"detail": "tampered"})
    assert AuditChain.verify(records) == 0


def test_verify_detects_hash_mismatch() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    record = log.append("operator", "pause", "run-1")
    broken = record.model_copy(update={"hash": "f" * 64})
    assert AuditChain.verify([broken]) == 0


def test_verify_detects_reordering() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    log.append("operator", "pause", "run-1")
    log.append("operator", "stop", "run-1")
    records = log.records()
    assert AuditChain.verify([records[1], records[0]]) == 0
