"""Tests for attestation signing and verification (DD-10, T9)."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import (
    generate_keypair,
    load_or_generate_keypair,
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


def test_persisted_keypair_verifies_after_reload(tmp_path: Path) -> None:
    key_file = tmp_path / "keys" / "attestation.pem"
    private_key, public_key = load_or_generate_keypair(key_file)
    signed = sign_attestation(_attestation(), private_key)

    _, reloaded_public = load_or_generate_keypair(key_file)

    assert verify_attestation(signed, public_key) is True
    assert verify_attestation(signed, reloaded_public) is True


def test_persisted_keypair_is_reused_on_reload(tmp_path: Path) -> None:
    key_file = tmp_path / "attestation.pem"
    first, _ = load_or_generate_keypair(key_file)
    second, _ = load_or_generate_keypair(key_file)

    assert first.private_bytes_raw() == second.private_bytes_raw()


def test_key_file_is_created_with_restrictive_permissions(tmp_path: Path) -> None:
    key_file = tmp_path / "attestation.pem"

    load_or_generate_keypair(key_file)

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
