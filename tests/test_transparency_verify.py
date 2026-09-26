"""Tests for public attestation verification (M35-02)."""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import generate_keypair, sign_attestation
from hiveplane.transparency import InMemoryTransparencyStore, TransparencyLog
from hiveplane.transparency.verify import PublicVerification, PublicVerifier


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
        "signer": {"identity": "cert@hiveplane", "key_id": "key-1", "signature": "unsigned"},
    }
    payload.update(overrides)
    return payload


def _signed(private_key: Any = None, **overrides: Any) -> Attestation:
    if private_key is None:
        private_key, _ = generate_keypair()
    return sign_attestation(Attestation.model_validate(_payload(**overrides)), private_key)


def _verifier(
    attestation: Attestation,
    *,
    public_key: Ed25519PublicKey,
    log: TransparencyLog | None = None,
) -> PublicVerifier:
    store = {attestation.attestation_id: attestation}
    return PublicVerifier(
        get_attestation=store.get,
        log=log if log is not None else TransparencyLog(InMemoryTransparencyStore()),
        key_resolver=lambda key_id: public_key if key_id == attestation.signer.key_id else None,
    )


def test_unknown_attestation_returns_none() -> None:
    _, public_key = generate_keypair()
    verifier = PublicVerifier(
        get_attestation=lambda _: None,
        log=TransparencyLog(InMemoryTransparencyStore()),
        key_resolver=lambda _: public_key,
    )

    assert verifier.verify("missing") is None


def test_valid_attestation_verifies_publicly() -> None:
    private_key, public_key = generate_keypair()
    attestation = _signed(private_key)
    log = TransparencyLog(InMemoryTransparencyStore())
    log.append(attestation)

    result = _verifier(attestation, public_key=public_key, log=log).verify("att-1")

    assert result is not None
    assert result.valid is True
    assert result.signer_key_id == "key-1"
    assert result.status == "certified"
    assert result.issued_at == attestation.timestamp
    assert result.log_seq == 0
    assert result.chain_valid is True
    assert result.chain_length == 1


def test_tampered_attestation_reports_invalid() -> None:
    private_key, public_key = generate_keypair()
    attestation = _signed(private_key)
    log = TransparencyLog(InMemoryTransparencyStore())
    log.append(attestation)
    tampered = attestation.model_copy(update={"model_identity": "evil-model"})

    result = _verifier(tampered, public_key=public_key, log=log).verify("att-1")

    assert result is not None
    assert result.valid is False
    assert result.reason is not None


def test_attestation_absent_from_the_log_is_invalid() -> None:
    private_key, public_key = generate_keypair()
    attestation = _signed(private_key)

    result = _verifier(
        attestation, public_key=public_key, log=TransparencyLog(InMemoryTransparencyStore())
    ).verify("att-1")

    assert result is not None
    assert result.valid is False
    assert result.log_seq is None
    assert result.chain_valid is True


def test_tampered_chain_reports_invalid() -> None:
    private_key, public_key = generate_keypair()
    attestation = _signed(private_key)
    store = InMemoryTransparencyStore()
    log = TransparencyLog(store)
    log.append(attestation)
    original = store.list_entries()[0]
    store.add_entry(original.model_copy(update={"entry_hash": "deadbeef"}))

    result = _verifier(attestation, public_key=public_key, log=log).verify("att-1")

    assert result is not None
    assert result.valid is False
    assert result.chain_valid is False


def test_unknown_signing_key_is_invalid() -> None:
    private_key, _ = generate_keypair()
    attestation = _signed(private_key)
    log = TransparencyLog(InMemoryTransparencyStore())
    log.append(attestation)
    verifier = PublicVerifier(
        get_attestation=lambda _: attestation,
        log=log,
        key_resolver=lambda _: None,
    )

    result = verifier.verify("att-1")

    assert result is not None
    assert result.valid is False


def test_public_verification_exposes_no_workload_internals() -> None:
    private_key, public_key = generate_keypair()
    attestation = _signed(private_key)
    log = TransparencyLog(InMemoryTransparencyStore())
    log.append(attestation)

    result = _verifier(attestation, public_key=public_key, log=log).verify("att-1")

    assert result is not None
    exposed = set(PublicVerification.model_fields)
    assert exposed == {
        "attestation_id",
        "valid",
        "signer_key_id",
        "issued_at",
        "status",
        "target_context",
        "log_seq",
        "chain_valid",
        "chain_length",
        "reason",
    }
