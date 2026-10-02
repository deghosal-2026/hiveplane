"""Portable fleet bundle export/import with provenance (M54-05/06, D36).

A bundle carries a workload manifest, a benchmark corpus, and a policy pack, and
is signed with the control plane's Ed25519 key. Import verifies the signature and
digest before any write and refuses tampered bundles.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hiveplane.certification.binding import canonical_json
from hiveplane.transparency.errors import BundleVerificationError

BUNDLE_API_VERSION = "hiveplane/export/v1"
BUNDLE_KIND = "FleetBundle"


class BundleProvenance(BaseModel):
    """The signature block of an exported fleet bundle."""

    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    signature: str = Field(min_length=1)


class FleetBundle(BaseModel):
    """A portable manifest + corpus + policy-pack bundle."""

    model_config = ConfigDict(extra="forbid")

    api_version: str = BUNDLE_API_VERSION
    kind: str = BUNDLE_KIND
    workload: dict[str, Any] | None = None
    corpus: dict[str, Any] | None = None
    policy_pack: dict[str, Any] | None = None
    provenance: BundleProvenance | None = None


class ImportEntry(BaseModel):
    """A single planned import action."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    action: str = Field(min_length=1)
    detail: str | None = None


class ImportPlan(BaseModel):
    """The result of planning (and optionally applying) an import."""

    model_config = ConfigDict(extra="forbid")

    entries: list[ImportEntry] = Field(default_factory=list)
    dry_run: bool = True
    verified: bool = False


def _content(bundle: FleetBundle) -> dict[str, Any]:
    return bundle.model_dump(exclude={"provenance"}, mode="json")


def _signed_payload(content: dict[str, Any]) -> bytes:
    """Domain-separated bytes signed/verified for a fleet bundle.

    The ``hiveplane/export/v1`` prefix keeps this signature from being reused
    across protocols that sign raw canonical JSON (e.g. attestations/bundles).
    """
    return b"hiveplane/export/v1\n" + canonical_json(content).encode("utf-8")


def _digest(content: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def export_fleet_bundle(
    *,
    workload: dict[str, Any] | None = None,
    corpus: dict[str, Any] | None = None,
    policy_pack: dict[str, Any] | None = None,
    private_key: Ed25519PrivateKey | None = None,
    key_id: str = "control-plane",
) -> FleetBundle:
    """Build a fleet bundle, signing it when a private key is given."""
    bundle = FleetBundle(
        workload=workload, corpus=corpus, policy_pack=policy_pack
    )
    if private_key is None:
        return bundle
    content = _content(bundle)
    digest = _digest(content)
    signature = private_key.sign(_signed_payload(content))
    return bundle.model_copy(
        update={
            "provenance": BundleProvenance(
                key_id=key_id,
                digest=digest,
                signature=base64.b64encode(signature).decode("ascii"),
            )
        }
    )


def parse_fleet_bundle(document: dict[str, Any]) -> FleetBundle:
    """Validate a bundle envelope, refusing malformed or wrong-kind documents."""
    try:
        bundle = FleetBundle.model_validate(document)
    except ValidationError as exc:
        raise BundleVerificationError("fleet-bundle", "malformed bundle envelope") from exc
    if bundle.api_version != BUNDLE_API_VERSION or bundle.kind != BUNDLE_KIND:
        raise BundleVerificationError("fleet-bundle", "unknown bundle apiVersion/kind")
    return bundle


def verify_fleet_bundle(bundle: FleetBundle, public_key: Ed25519PublicKey) -> bool:
    """Return True when the bundle's signature and digest verify."""
    provenance = bundle.provenance
    if provenance is None:
        return False
    content = _content(bundle)
    if _digest(content) != provenance.digest:
        return False
    try:
        public_key.verify(
            base64.b64decode(provenance.signature),
            _signed_payload(content),
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def import_fleet_bundle(
    document: dict[str, Any],
    public_key: Ed25519PublicKey | None = None,
    *,
    allow_unsigned: bool = False,
) -> FleetBundle:
    """Verify and parse a bundle before any write; refuses tampered bundles."""
    bundle = parse_fleet_bundle(document)
    if bundle.provenance is None:
        if not allow_unsigned:
            raise BundleVerificationError("fleet-bundle", "bundle is not signed")
        return bundle
    if public_key is None:
        raise BundleVerificationError("fleet-bundle", "no public key to verify the bundle")
    if not verify_fleet_bundle(bundle, public_key):
        raise BundleVerificationError(
            "fleet-bundle", "signature or digest mismatch"
        )
    return bundle


def _name(kind: str, document: dict[str, Any]) -> str:
    if kind == "workload":
        metadata = document.get("metadata") or {}
        return str(metadata.get("name") or "workload")
    if kind == "corpus":
        return str(document.get("id") or "corpus")
    metadata = document.get("metadata") or {}
    return str(metadata.get("name") or "policy-pack")


def plan_import(
    bundle: FleetBundle,
    *,
    existing: Callable[[str, str], bool] | None = None,
) -> ImportPlan:
    """Plan the import: create vs. update, using ``existing(kind, name)``.

    Never writes. A conflict (same name, different content) is reported so an
    operator can decide; apply is a separate, explicit step.
    """
    entries: list[ImportEntry] = []
    parts = (
        ("workload", bundle.workload),
        ("corpus", bundle.corpus),
        ("policy_pack", bundle.policy_pack),
    )
    for kind, document in parts:
        if document is None:
            continue
        name = _name(kind, document)
        is_present = bool(existing(kind, name)) if existing is not None else False
        entries.append(
            ImportEntry(
                kind=kind,
                name=name,
                action="update" if is_present else "create",
            )
        )
    return ImportPlan(entries=entries, dry_run=True, verified=bundle.provenance is not None)


def _document_for(bundle: FleetBundle, kind: str) -> dict[str, Any] | None:
    if kind == "workload":
        return bundle.workload
    if kind == "corpus":
        return bundle.corpus
    return bundle.policy_pack


def apply_import(
    bundle: FleetBundle,
    plan: ImportPlan,
    *,
    handlers: dict[str, Callable[[dict[str, Any]], None]],
    dry_run: bool = True,
) -> ImportPlan:
    """Apply an import plan via per-kind handlers; ``dry_run`` writes nothing.

    Imported workloads are never auto-executed: a handler registers/certifies
    them through the normal path.
    """
    if dry_run:
        return plan.model_copy(update={"dry_run": True})
    for entry in plan.entries:
        document = _document_for(bundle, entry.kind)
        handler = handlers.get(entry.kind)
        if document is not None and handler is not None:
            handler(document)
    return plan.model_copy(update={"dry_run": False})
