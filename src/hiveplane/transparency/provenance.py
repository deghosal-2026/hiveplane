"""Workload bundle provenance: sign and verify agent code identity (M35-03).

A workload *bundle* binds a registered manifest to the bytes about to execute.
It is signed by the control plane at registration and verified at admission and
on export/import: a tampered or swapped bundle fails verification.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hiveplane.certification.binding import canonical_json, compute_binding
from hiveplane.core.workload import AgentWorkload
from hiveplane.transparency.errors import BundleVerificationError

#: Placeholder written before signing; the canonical payload excludes it.
UNSIGNED = "unsigned"

#: Envelope identifiers for exported/imported agent bundles (M35-05).
BUNDLE_API_VERSION = "hiveplane/bundle/v1"
BUNDLE_KIND = "AgentBundle"


class WorkloadBundle(BaseModel):
    """A signed agent bundle (manifest identity + entrypoint digest)."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    bundle_digest: str = Field(min_length=1)
    entrypoint_digest: str = Field(min_length=1)
    manifest_hash: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    registered_at: datetime


def entrypoint_digest(entrypoint: str, *, source: bytes | None = None) -> str:
    """Return the sha256 digest of the entrypoint source (or its reference).

    When the actual module/file bytes are available they are hashed; otherwise
    the declared entrypoint reference is hashed as a stable placeholder.
    """
    material = source if source is not None else entrypoint.encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def compute_bundle_digest(
    manifest: AgentWorkload, *, entrypoint_source: bytes | None = None
) -> str:
    """Return the deterministic digest identifying an agent bundle."""
    runtime = manifest.spec.runtime
    binding = compute_binding(manifest)
    payload = {
        "workload_id": manifest.name,
        "adapter": runtime.adapter.value,
        "entrypoint": runtime.entrypoint,
        "entrypoint_digest": entrypoint_digest(
            runtime.entrypoint, source=entrypoint_source
        ),
        "manifest_hash": binding.manifest_hash,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_bundle_payload(bundle: WorkloadBundle) -> bytes:
    """Return the canonical bytes covered by a bundle signature."""
    document = bundle.model_dump(mode="json", exclude={"signature"})
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_bundle(
    manifest: AgentWorkload,
    private_key: Ed25519PrivateKey,
    *,
    key_id: str,
    workload_id: str,
    registered_at: datetime,
    entrypoint_source: bytes | None = None,
) -> WorkloadBundle:
    """Build and sign the bundle for a manifest."""
    binding = compute_binding(manifest)
    runtime = manifest.spec.runtime
    bundle = WorkloadBundle(
        workload_id=workload_id,
        bundle_digest=compute_bundle_digest(
            manifest, entrypoint_source=entrypoint_source
        ),
        entrypoint_digest=entrypoint_digest(
            runtime.entrypoint, source=entrypoint_source
        ),
        manifest_hash=binding.manifest_hash,
        key_id=key_id,
        signature=UNSIGNED,
        registered_at=registered_at,
    )
    signature = private_key.sign(canonical_bundle_payload(bundle))
    return bundle.model_copy(update={"signature": base64.b64encode(signature).decode("ascii")})


def verify_bundle(
    bundle: WorkloadBundle,
    manifest: AgentWorkload,
    public_key: Ed25519PublicKey,
    *,
    entrypoint_source: bytes | None = None,
) -> bool:
    """Return True if the bundle signature and digest match the manifest."""
    expected = compute_bundle_digest(manifest, entrypoint_source=entrypoint_source)
    if bundle.bundle_digest != expected:
        return False
    try:
        signature = base64.b64decode(bundle.signature, validate=True)
    except (binascii.Error, ValueError):
        return False
    try:
        public_key.verify(signature, canonical_bundle_payload(bundle))
    except InvalidSignature:
        return False
    return True


def export_bundle(manifest: AgentWorkload, bundle: WorkloadBundle) -> dict[str, Any]:
    """Return a self-contained, signed agent-bundle envelope for export."""
    return {
        "apiVersion": BUNDLE_API_VERSION,
        "kind": BUNDLE_KIND,
        "workloadId": bundle.workload_id,
        "manifest": manifest.model_dump(mode="json", by_alias=True),
        "bundle": bundle.model_dump(mode="json"),
    }


def import_bundle(
    document: dict[str, Any],
    public_key: Ed25519PublicKey,
    *,
    entrypoint_source: bytes | None = None,
) -> WorkloadBundle:
    """Validate an exported bundle envelope, refusing tampered imports.

    Raises :class:`BundleVerificationError` when the envelope is malformed or the
    manifest/signature/digest do not verify against the supplied public key.
    """
    workload_id = str(document.get("workloadId", "?"))
    try:
        manifest = AgentWorkload.model_validate(document["manifest"])
        bundle = WorkloadBundle.model_validate(document["bundle"])
    except (KeyError, TypeError, ValidationError) as exc:
        raise BundleVerificationError(workload_id, "malformed bundle envelope") from exc
    if not verify_bundle(
        bundle, manifest, public_key, entrypoint_source=entrypoint_source
    ):
        raise BundleVerificationError(workload_id, "signature or digest mismatch")
    return bundle
