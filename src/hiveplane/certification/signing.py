"""Attestation signing and verification (DD-10, T9).

Certifications produce signed attestations. The signature covers the canonical
JSON serialization of every attestation field except ``signer.signature``
itself, so any consumer can verify a record with the public key.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from hiveplane.certification.models import Attestation


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate an Ed25519 signing key pair."""
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def load_or_generate_keypair(
    path: str | Path,
) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Load a PEM signing key from ``path``, generating it once if absent.

    The generated private key is written with owner-only (``0600``) permissions
    so that attestations remain verifiable across restarts (T9).
    """
    key_path = Path(path)
    if key_path.exists():
        private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise ValueError(f"signing key at {str(key_path)!r} is not an Ed25519 private key")
        return private_key, private_key.public_key()
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(pem)
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
