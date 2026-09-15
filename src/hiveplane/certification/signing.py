"""Attestation signing and verification (DD-10, T9).

Certifications produce signed attestations. The signature covers the canonical
JSON serialization of every attestation field except ``signer.signature``
itself, so any consumer can verify a record with the public key.
"""

from __future__ import annotations

import base64
import binascii
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from hiveplane.certification.models import Attestation


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate an Ed25519 signing key pair."""
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def canonical_payload(attestation: Attestation) -> bytes:
    """Return the canonical bytes that the signature covers."""
    document = attestation.model_dump(
        mode="json",
        by_alias=True,
        exclude={"signer": {"signature"}},
    )
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_attestation(attestation: Attestation, private_key: Ed25519PrivateKey) -> Attestation:
    """Return a copy of the attestation with a valid signature."""
    signature = private_key.sign(canonical_payload(attestation))
    encoded = base64.b64encode(signature).decode("ascii")
    signer = attestation.signer.model_copy(update={"signature": encoded})
    return attestation.model_copy(update={"signer": signer})


def verify_attestation(attestation: Attestation, public_key: Ed25519PublicKey) -> bool:
    """Return True if the attestation signature is valid for the public key."""
    try:
        signature = base64.b64decode(attestation.signer.signature, validate=True)
    except (binascii.Error, ValueError):
        return False
    try:
        public_key.verify(signature, canonical_payload(attestation))
    except InvalidSignature:
        return False
    return True
