"""Tests for attestation signing and verification (DD-10, T9)."""

from __future__ import annotations

from typing import Any

from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import (
    generate_keypair,
    sign_attestation,
    verify_attestation,
)


def _attestation(**overrides: Any) -> Attestation:
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
            "control_plane_version": "0.1.0",
        },
        "signer": {"identity": "cert@hiveplane", "key_id": "key-1", "signature": "pending"},
    }
    payload.update(overrides)
    return Attestation.model_validate(payload)


def test_sign_then_verify() -> None:
    private_key, public_key = generate_keypair()

    signed = sign_attestation(_attestation(), private_key)

    assert signed.signer.signature != "pending"
    assert verify_attestation(signed, public_key) is True


def test_tampered_attestation_fails_verification() -> None:
    private_key, public_key = generate_keypair()
    signed = sign_attestation(_attestation(), private_key)

    tampered = signed.model_copy(update={"model_identity": "evil-model"})

    assert verify_attestation(tampered, public_key) is False


def test_wrong_public_key_fails_verification() -> None:
    private_key, _ = generate_keypair()
    _, other_public = generate_keypair()
    signed = sign_attestation(_attestation(), private_key)

    assert verify_attestation(signed, other_public) is False


def test_unsigned_placeholder_signature_fails_verification() -> None:
    _, public_key = generate_keypair()

    assert verify_attestation(_attestation(), public_key) is False


def test_signature_is_deterministic_for_same_payload() -> None:
    private_key, _ = generate_keypair()
    first = sign_attestation(_attestation(), private_key)
    second = sign_attestation(_attestation(), private_key)

    assert first.signer.signature == second.signer.signature
