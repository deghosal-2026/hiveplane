"""Tests for the tamper-evident audit chain (no database required)."""

from __future__ import annotations

from collections.abc import Callable
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


def _clocked(*times: datetime) -> Callable[[], datetime]:
    remaining = list(times)
    return lambda: remaining.pop(0)


def test_anchor_defaults_to_null_hash() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    assert log.anchor() == "0" * 64


def test_prune_removes_expired_prefix_and_keeps_chain_verifiable() -> None:
    log = InMemoryAuditLog(
        clock=_clocked(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
        )
    )
    first = log.append("operator", "a", "run-1")
    second = log.append("operator", "b", "run-1")
    log.append("operator", "c", "run-1")

    pruned = log.prune(before=datetime(2026, 1, 2, 12, tzinfo=UTC))

    assert pruned == 2
    assert [record.action for record in log.records()] == ["c"]
    assert log.anchor() == second.hash
    assert log.anchor() != first.hash
    assert log.verify() is True


def test_prune_stops_at_protected_record() -> None:
    log = InMemoryAuditLog(
        clock=_clocked(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
            datetime(2026, 1, 4, tzinfo=UTC),
        )
    )
    for action in ("a", "b", "c", "d"):
        log.append("operator", action, "run-1")

    pruned = log.prune(
        before=datetime(2026, 1, 5, tzinfo=UTC),
        protect=lambda record: record.action == "b",
    )

    assert pruned == 1
    assert [record.action for record in log.records()] == ["b", "c", "d"]
    assert log.verify() is True


def test_prune_with_nothing_expired_is_a_noop() -> None:
    log = InMemoryAuditLog(clock=lambda: _NOW)
    log.append("operator", "a", "run-1")

    assert log.prune(before=datetime(2025, 1, 1, tzinfo=UTC)) == 0
    assert log.anchor() == "0" * 64
    assert [record.action for record in log.records()] == ["a"]
    assert log.verify() is True


def test_append_after_prune_links_to_anchor() -> None:
    log = InMemoryAuditLog(
        clock=_clocked(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
        )
    )
    log.append("operator", "a", "run-1")
    log.append("operator", "b", "run-1")
    log.prune(before=datetime(2026, 1, 3, tzinfo=UTC))
    anchor = log.anchor()

    appended = log.append("operator", "c", "run-1")

    assert appended.prev_hash == anchor
    assert log.verify() is True

