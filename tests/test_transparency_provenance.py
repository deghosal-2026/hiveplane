"""Tests for workload bundle provenance signing (M35-03)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.certification.signing import generate_keypair
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.transparency.errors import BundleVerificationError
from hiveplane.transparency.provenance import (
    compute_bundle_digest,
    sign_bundle,
    verify_bundle,
)

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _manifest(**overrides: Any) -> AgentWorkload:
    manifest: dict[str, Any] = {
        "apiVersion": "hiveplane/v1",
        "kind": "AgentWorkload",
        "metadata": {"name": "repo-agent", "owner": "platform-team"},
        "spec": {
            "runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"},
            "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0},
            "model": {
                "strategy": "fixed",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
        },
    }
    manifest.update(overrides)
    return AgentWorkload.model_validate(manifest)


def test_bundle_digest_is_deterministic() -> None:
    manifest = _manifest()

    assert compute_bundle_digest(manifest) == compute_bundle_digest(manifest)


def test_bundle_digest_changes_with_entrypoint_source() -> None:
    manifest = _manifest()

    original = compute_bundle_digest(manifest, entrypoint_source=b"def run(): ...")
    changed = compute_bundle_digest(manifest, entrypoint_source=b"def run(): return 'evil'")

    assert original != changed


def test_bundle_digest_changes_when_behavior_changes() -> None:
    original = compute_bundle_digest(_manifest())
    changed = compute_bundle_digest(
        _manifest(
            spec={
                "runtime": {"adapter": "raw-worker", "entrypoint": "examples.other:run"},
                "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0},
                "model": {
                    "strategy": "fixed",
                    "identity": {
                        "provider": "openai",
                        "family": "gpt-4o",
                        "version": "2024-08-06",
                    },
                },
            }
        )
    )

    assert original != changed


def test_sign_then_verify_bundle() -> None:
    manifest = _manifest()
    private_key, public_key = generate_keypair()
    bundle = sign_bundle(
        manifest,
        private_key,
        key_id="key-1",
        workload_id="repo-agent",
        registered_at=_NOW,
    )

    assert bundle.signature != "unsigned"
    assert verify_bundle(bundle, manifest, public_key) is True


def test_tampered_bundle_digest_fails_verification() -> None:
    manifest = _manifest()
    private_key, public_key = generate_keypair()
    bundle = sign_bundle(
        manifest, private_key, key_id="key-1", workload_id="repo-agent", registered_at=_NOW
    )

    tampered = bundle.model_copy(update={"bundle_digest": "deadbeef"})

    assert verify_bundle(tampered, manifest, public_key) is False


def test_tampered_signature_fails_verification() -> None:
    manifest = _manifest()
    private_key, public_key = generate_keypair()
    bundle = sign_bundle(
        manifest, private_key, key_id="key-1", workload_id="repo-agent", registered_at=_NOW
    )

    tampered = bundle.model_copy(update={"signature": "AAAA"})

    assert verify_bundle(tampered, manifest, public_key) is False


def test_verification_fails_against_a_changed_manifest() -> None:
    manifest = _manifest()
    private_key, public_key = generate_keypair()
    bundle = sign_bundle(
        manifest, private_key, key_id="key-1", workload_id="repo-agent", registered_at=_NOW
    )
    swapped = _manifest(
        spec={
            "runtime": {"adapter": "raw-worker", "entrypoint": "examples.evil:run"},
            "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0},
            "model": {
                "strategy": "fixed",
                "identity": {
                    "provider": "openai",
                    "family": "gpt-4o",
                    "version": "2024-08-06",
                },
            },
        }
    )

    assert verify_bundle(bundle, swapped, public_key) is False


def test_verification_fails_against_a_different_manifest_with_source() -> None:
    manifest = _manifest()
    private_key, public_key = generate_keypair()
    bundle = sign_bundle(
        manifest,
        private_key,
        key_id="key-1",
        workload_id="repo-agent",
        registered_at=_NOW,
        entrypoint_source=b"def run(): ...",
    )

    assert verify_bundle(bundle, manifest, public_key, entrypoint_source=b"evil") is False
    assert (
        verify_bundle(bundle, manifest, public_key, entrypoint_source=b"def run(): ...") is True
    )


def test_registration_signs_a_verifiable_bundle(make_manifest: Any) -> None:
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(),
        attestation_public_key=public_key,
        bundle_signing_key=private_key,
        clock=lambda: _NOW,
    )
    record = registry.create(make_manifest(name="repo-agent"))

    assert record.bundle is not None
    assert record.bundle.key_id
    assert verify_bundle(record.bundle, record.manifest, public_key) is True


def test_registration_without_a_signing_key_records_no_bundle(make_manifest: Any) -> None:
    _, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(), attestation_public_key=public_key, clock=lambda: _NOW
    )
    record = registry.create(make_manifest(name="repo-agent"))

    assert record.bundle is None


def _envelope(manifest: AgentWorkload, private_key: Any, public_key: Any) -> dict[str, Any]:
    from hiveplane.transparency.provenance import export_bundle

    bundle = sign_bundle(
        manifest,
        private_key,
        key_id="key-1",
        workload_id=manifest.name,
        registered_at=_NOW,
    )
    return export_bundle(manifest, bundle)


def test_exported_bundle_imports_and_verifies() -> None:
    from hiveplane.transparency.provenance import import_bundle

    manifest = _manifest()
    private_key, public_key = generate_keypair()

    document = _envelope(manifest, private_key, public_key)
    bundle = import_bundle(document, public_key)

    assert bundle.workload_id == "repo-agent"
    assert verify_bundle(bundle, manifest, public_key) is True


def test_import_rejects_a_tampered_manifest() -> None:
    from hiveplane.transparency.provenance import import_bundle

    manifest = _manifest()
    private_key, public_key = generate_keypair()
    document = _envelope(manifest, private_key, public_key)
    document["manifest"]["spec"]["runtime"]["entrypoint"] = "examples.evil:run"

    with pytest.raises(BundleVerificationError):
        import_bundle(document, public_key)


def test_import_rejects_a_tampered_signature() -> None:
    from hiveplane.transparency.provenance import import_bundle

    manifest = _manifest()
    private_key, public_key = generate_keypair()
    document = _envelope(manifest, private_key, public_key)
    document["bundle"]["signature"] = "AAAA"

    with pytest.raises(BundleVerificationError):
        import_bundle(document, public_key)


def test_import_rejects_a_tampered_digest() -> None:
    from hiveplane.transparency.provenance import import_bundle

    manifest = _manifest()
    private_key, public_key = generate_keypair()
    document = _envelope(manifest, private_key, public_key)
    document["bundle"]["bundle_digest"] = "deadbeef"

    with pytest.raises(BundleVerificationError):
        import_bundle(document, public_key)


def test_verify_bundle_rejects_invalid_base64_signature() -> None:
    manifest = _manifest()
    private_key, public_key = generate_keypair()
    bundle = sign_bundle(
        manifest, private_key, key_id="key-1", workload_id="repo-agent", registered_at=_NOW
    )

    tampered = bundle.model_copy(update={"signature": "!!!"})

    assert verify_bundle(tampered, manifest, public_key) is False


def test_import_bundle_rejects_a_malformed_envelope() -> None:
    from hiveplane.transparency.provenance import import_bundle

    _, public_key = generate_keypair()

    with pytest.raises(BundleVerificationError):
        import_bundle({}, public_key)
