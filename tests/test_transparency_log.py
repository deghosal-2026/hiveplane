"""Tests for the attestation transparency log (M35-01)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import generate_keypair, sign_attestation
from hiveplane.config import Settings
from hiveplane.persistence.base import create_engine_from_settings
from hiveplane.transparency import (
    ZERO_HASH,
    DuplicateLogEntryError,
    InMemoryTransparencyStore,
    PostgresTransparencyStore,
    TransparencyEntry,
    TransparencyLog,
    build_transparency_store,
    compute_entry_hash,
)
from postgres import ensure_schema


def _attestation(attestation_id: str = "att-1", **overrides: Any) -> Attestation:
    payload: dict[str, Any] = {
        "attestation_id": attestation_id,
        "workload_id": "repo-agent",
        "manifest_version": 1,
        "benchmark_version": "1.0.0",
        "benchmark_run_id": "br-1",
        "corpus_id": "corpus",
        "corpus_version": 1,
        "model_identity": "gpt-4o-2024-08-06",
        "status": "certified",
        "target_context": "production",
        "eval_summary": {
            "pass_rate": 0.95,
            "critical_failures": 0,
            "p95_latency_ms": 1000,
            "tasks_passed": 19,
            "tasks_failed": 1,
        },
        "timestamp": "2026-09-12T10:00:00Z",
        "environment": {
            "sandbox_image": "img",
            "runtime_adapter": "raw-worker",
            "control_plane_version": "0.2.0",
        },
        "signer": {"identity": "cert@hiveplane", "key_id": "key-1", "signature": "unsigned"},
    }
    payload.update(overrides)
    return Attestation.model_validate(payload)


def _signed(attestation_id: str = "att-1") -> Attestation:
    private_key, _ = generate_keypair()
    return sign_attestation(_attestation(attestation_id), private_key)


def _log() -> TransparencyLog:
    return TransparencyLog(InMemoryTransparencyStore())


def test_genesis_entry_links_to_zero_hash() -> None:
    log = _log()

    entry = log.append(_signed("att-1"))

    assert entry.seq == 0
    assert entry.prev_hash == ZERO_HASH


def test_append_links_entries_into_a_hash_chain() -> None:
    log = _log()

    first = log.append(_signed("att-1"))
    second = log.append(_signed("att-2"))
    third = log.append(_signed("att-3"))

    assert [first.seq, second.seq, third.seq] == [0, 1, 2]
    assert second.prev_hash == first.entry_hash
    assert third.prev_hash == second.entry_hash


def test_entry_hash_matches_the_documented_formula() -> None:
    log = _log()
    attestation = _signed("att-1")

    entry = log.append(attestation)

    expected = compute_entry_hash(prev_hash=ZERO_HASH, seq=0, attestation=attestation)
    assert entry.entry_hash == expected


def test_appending_the_same_attestation_twice_is_rejected() -> None:
    log = _log()
    attestation = _signed("att-1")
    log.append(attestation)

    try:
        log.append(attestation)
    except DuplicateLogEntryError:
        pass
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected DuplicateLogEntryError")


def test_verify_chain_accepts_an_untouched_log() -> None:
    log = _log()
    log.append(_signed("att-1"))
    log.append(_signed("att-2"))
    log.append(_signed("att-3"))

    verification = log.verify_chain()

    assert verification.valid is True
    assert verification.length == 3
    assert verification.tampered_at is None


def test_verify_chain_detects_a_tampered_entry_hash() -> None:
    store = InMemoryTransparencyStore()
    log = TransparencyLog(store)
    log.append(_signed("att-1"))
    log.append(_signed("att-2"))
    original = store.list_entries()[1]
    tampered = original.model_copy(update={"entry_hash": "deadbeef"})
    store.add_entry(tampered)

    verification = log.verify_chain()

    assert verification.valid is False
    assert verification.tampered_at == 1


def test_verify_chain_detects_a_swapped_attestation() -> None:
    store = InMemoryTransparencyStore()
    log = TransparencyLog(store)
    log.append(_signed("att-1"))
    original = store.list_entries()[0]
    swapped = original.model_copy(
        update={"attestation": original.attestation.model_copy(update={"model_identity": "evil"})}
    )
    store.add_entry(swapped)

    verification = log.verify_chain()

    assert verification.valid is False
    assert verification.tampered_at == 0


def test_verify_chain_detects_a_deleted_entry() -> None:
    store = InMemoryTransparencyStore()
    log = TransparencyLog(store)
    first = log.append(_signed("att-1"))
    log.append(_signed("att-2"))
    third = log.append(_signed("att-3"))
    store.delete_entry("att-2")

    verification = log.verify_chain()

    assert verification.valid is False
    assert first.seq == 0
    assert third.seq == 2


def test_proof_for_unknown_attestation_returns_none() -> None:
    log = _log()
    log.append(_signed("att-1"))

    assert log.prove("missing") is None


def test_proof_reports_inclusion_in_a_valid_chain() -> None:
    log = _log()
    log.append(_signed("att-1"))
    log.append(_signed("att-2"))

    proof = log.prove("att-2")

    assert proof is not None
    assert proof.attestation_id == "att-2"
    assert proof.valid is True
    assert proof.entry.seq == 1


def test_in_memory_store_returns_copies_not_references() -> None:
    store = InMemoryTransparencyStore()
    log = TransparencyLog(store)
    log.append(_signed("att-1"))

    first = store.list_entries()[0]
    second = store.list_entries()[0]

    assert first is not second


def test_latest_returns_the_highest_sequence_entry() -> None:
    log = _log()
    log.append(_signed("att-1"))
    last = log.append(_signed("att-2"))

    assert log.latest() == last


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_transparency_store(Settings()), InMemoryTransparencyStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_transparency_store(settings), PostgresTransparencyStore)


def test_postgres_round_trip_preserves_the_chain(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresTransparencyStore(pg_engine)
    store.clear()
    log = TransparencyLog(store)
    log.append(_signed("att-1"))
    log.append(_signed("att-2"))

    reopened = TransparencyLog(PostgresTransparencyStore(pg_engine))

    assert reopened.verify_chain().valid is True
    assert [entry.attestation_id for entry in reopened.entries()] == ["att-1", "att-2"]
    assert reopened.prove("att-2") is not None


def test_postgres_get_missing_entry_returns_none(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    ensure_schema(pg_engine)
    store = PostgresTransparencyStore(pg_engine)
    store.clear()

    assert store.get_entry("missing") is None
    assert store.latest() is None


def test_postgres_store_survives_new_engine(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    ensure_schema(pg_engine)
    store = PostgresTransparencyStore(pg_engine)
    store.clear()
    TransparencyLog(store).append(_signed("att-restart"))

    fresh_engine = create_engine_from_settings()
    try:
        reopened = PostgresTransparencyStore(fresh_engine)
        assert reopened.get_entry("att-restart") is not None
    finally:
        fresh_engine.dispose()



class _StaticStore:
    """A minimal store that returns pre-built entries (for branch coverage)."""

    def __init__(self, entries: list[Any]) -> None:
        self._entries = entries

    def add_entry(self, entry: Any) -> None:
        self._entries.append(entry)

    def get_entry(self, attestation_id: str) -> Any:
        return None

    def list_entries(self) -> list[Any]:
        return list(self._entries)

    def delete_entry(self, attestation_id: str) -> None:
        return None

    def latest(self) -> Any:
        return self._entries[-1] if self._entries else None

    def clear(self) -> None:
        self._entries.clear()


def test_in_memory_store_clear_and_missing() -> None:
    store = InMemoryTransparencyStore()
    log = TransparencyLog(store)
    log.append(_signed("att-1"))

    store.clear()

    assert store.latest() is None
    assert store.get_entry("att-1") is None
    assert store.list_entries() == []


def test_verify_chain_detects_a_broken_link() -> None:
    store = InMemoryTransparencyStore()
    log = TransparencyLog(store)
    log.append(_signed("att-1"))
    log.append(_signed("att-2"))
    second = store.list_entries()[1]
    store.add_entry(second.model_copy(update={"prev_hash": "bad"}))

    verification = log.verify_chain()

    assert verification.valid is False
    assert verification.tampered_at == 1


def test_verify_chain_detects_an_attestation_id_mismatch() -> None:
    attestation = _signed("att-1")
    entry = TransparencyEntry(
        seq=0,
        attestation_id="att-other",
        prev_hash=ZERO_HASH,
        entry_hash=compute_entry_hash(prev_hash=ZERO_HASH, seq=0, attestation=attestation),
        created_at=datetime(2026, 9, 12, tzinfo=UTC),
        attestation=attestation,
    )
    log = TransparencyLog(_StaticStore([entry]))

    verification = log.verify_chain()

    assert verification.valid is False
    assert verification.tampered_at == 0


def test_entry_requires_a_timezone_aware_created_at() -> None:
    with pytest.raises(ValidationError):
        TransparencyEntry(
            seq=0,
            attestation_id="att-1",
            prev_hash=ZERO_HASH,
            entry_hash="hash",
            created_at=datetime(2026, 9, 12),  # noqa: DTZ001 - naive on purpose
            attestation=_signed("att-1"),
        )


def test_postgres_delete_entry(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresTransparencyStore(pg_engine)
    store.clear()
    log = TransparencyLog(store)
    log.append(_signed("att-1"))
    log.append(_signed("att-2"))

    first = store.get_entry("att-1")
    assert first is not None
    store.add_entry(first)  # exercise the update branch
    store.delete_entry("att-2")
    store.delete_entry("missing")

    assert [entry.attestation_id for entry in store.list_entries()] == ["att-1"]
