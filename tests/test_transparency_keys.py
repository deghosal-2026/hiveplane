"""Tests for signing-key management, rotation, and distribution (M35-06)."""

from __future__ import annotations

from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import Engine

from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import (
    generate_keypair,
    sign_attestation,
    verify_attestation,
)
from hiveplane.config import Settings
from hiveplane.transparency.errors import UnknownSigningKeyError
from hiveplane.transparency.keys import (
    InMemorySigningKeyStore,
    KeyStatus,
    PostgresSigningKeyStore,
    SigningKeyRegistry,
    build_signing_key_store,
    decode_public_key,
    encode_public_key,
)
from hiveplane.transparency.log import TransparencyLog
from hiveplane.transparency.store import InMemoryTransparencyStore
from hiveplane.transparency.verify import PublicVerifier
from postgres import ensure_schema

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _registry() -> SigningKeyRegistry:
    return SigningKeyRegistry(InMemorySigningKeyStore(), clock=lambda: _NOW)


def test_public_key_encoding_round_trips() -> None:
    _, public_key = generate_keypair()

    decoded = decode_public_key(encode_public_key(public_key))

    assert isinstance(decoded, Ed25519PublicKey)
    assert decoded.public_bytes_raw() == public_key.public_bytes_raw()


def test_add_and_resolve_key() -> None:
    registry = _registry()
    _, public_key = generate_keypair()

    record = registry.add_key("key-1", public_key)

    assert record.status is KeyStatus.ACTIVE
    assert registry.active_key_id() == "key-1"
    assert registry.resolve("key-1") is not None


def test_resolve_unknown_key_returns_none() -> None:
    assert _registry().resolve("missing") is None


def test_rotate_retires_previous_and_activates_new() -> None:
    registry = _registry()
    _, first_public = generate_keypair()
    _, second_public = generate_keypair()
    registry.add_key("key-1", first_public)

    record = registry.rotate("key-2", second_public)

    assert record.status is KeyStatus.ACTIVE
    assert registry.active_key_id() == "key-2"
    keys = {key.key_id: key for key in registry.all_keys()}
    assert keys["key-1"].status is KeyStatus.RETIRED
    assert keys["key-1"].retired_at == _NOW


def test_retired_key_still_resolves_for_old_attestations() -> None:
    registry = _registry()
    _, first_public = generate_keypair()
    registry.add_key("key-1", first_public)

    registry.rotate("key-2", generate_keypair()[1])

    assert registry.resolve("key-1") is not None


def test_rotation_preserves_old_attestation_verification() -> None:
    registry = _registry()
    first_private, first_public = generate_keypair()
    registry.add_key("key-1", first_public)
    attestation = sign_attestation(_attestation(), first_private)

    registry.rotate("key-2", generate_keypair()[1])

    assert verify_attestation(attestation, registry.resolve("key-1")) is True  # type: ignore[arg-type]


def test_public_verify_uses_the_key_registry_for_rotated_keys() -> None:
    registry = _registry()
    first_private, first_public = generate_keypair()
    registry.add_key("key-1", first_public)
    attestation = sign_attestation(_attestation(), first_private)
    log = TransparencyLog(InMemoryTransparencyStore())
    log.append(attestation)
    registry.rotate("key-2", generate_keypair()[1])
    verifier = PublicVerifier(
        get_attestation=lambda _: attestation,
        log=log,
        key_resolver=registry.resolve,
    )

    result = verifier.verify("att-1")

    assert result is not None
    assert result.valid is True
    assert result.signer_key_id == "key-1"


def _attestation() -> Attestation:
    return Attestation.model_validate(
        {
            "attestation_id": "att-1",
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
            "signer": {
                "identity": "cert@hiveplane",
                "key_id": "key-1",
                "signature": "unsigned",
            },
        }
    )


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_signing_key_store(Settings()), InMemorySigningKeyStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_signing_key_store(settings), PostgresSigningKeyStore)


def test_postgres_key_store_round_trips(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresSigningKeyStore(pg_engine)
    store.clear()
    registry = SigningKeyRegistry(store, clock=lambda: _NOW)
    _, public_key = generate_keypair()
    registry.add_key("key-1", public_key)
    registry.rotate("key-2", generate_keypair()[1])

    reopened = SigningKeyRegistry(PostgresSigningKeyStore(pg_engine))

    assert reopened.active_key_id() == "key-2"
    assert reopened.resolve("key-1") is not None
    assert reopened.resolve("key-2") is not None
    assert reopened.all_keys()[0].key_id == "key-1"
    assert store.get_key("missing") is None


def test_in_memory_store_clear_and_missing_key() -> None:
    store = InMemorySigningKeyStore()
    assert store.get_key("missing") is None
    registry = SigningKeyRegistry(store, clock=lambda: _NOW)
    registry.add_key("key-1", generate_keypair()[1])

    store.clear()

    assert store.list_keys() == []


def test_rotate_with_no_active_key_just_adds() -> None:
    registry = _registry()

    record = registry.rotate("key-1", generate_keypair()[1])

    assert record.status is KeyStatus.ACTIVE
    assert registry.active_key_id() == "key-1"


def test_add_key_can_register_a_retired_key() -> None:
    registry = _registry()

    record = registry.add_key("old", generate_keypair()[1], status=KeyStatus.RETIRED)

    assert record.status is KeyStatus.RETIRED
    assert registry.active_key_id() is None


def test_unknown_signing_key_error_message() -> None:
    error = UnknownSigningKeyError("key-x")

    assert "key-x" in str(error)
    assert error.key_id == "key-x"
