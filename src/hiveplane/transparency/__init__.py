"""Attestation transparency, public verification, and workload provenance (M35)."""

from __future__ import annotations

from hiveplane.transparency.errors import (
    BundleVerificationError,
    DuplicateLogEntryError,
    TransparencyError,
    UnknownSigningKeyError,
)
from hiveplane.transparency.keys import (
    InMemorySigningKeyStore,
    KeyStatus,
    PostgresSigningKeyStore,
    SigningKeyRecord,
    SigningKeyRegistry,
    SigningKeyStore,
    build_signing_key_store,
    decode_public_key,
    encode_public_key,
)
from hiveplane.transparency.log import ZERO_HASH, TransparencyLog, compute_entry_hash
from hiveplane.transparency.models import (
    ChainVerification,
    TransparencyEntry,
    TransparencyProof,
)
from hiveplane.transparency.provenance import (
    BUNDLE_API_VERSION,
    BUNDLE_KIND,
    WorkloadBundle,
    compute_bundle_digest,
    export_bundle,
    import_bundle,
    sign_bundle,
    verify_bundle,
)
from hiveplane.transparency.store import (
    InMemoryTransparencyStore,
    PostgresTransparencyStore,
    TransparencyStore,
    build_transparency_store,
)
from hiveplane.transparency.verify import PublicVerification, PublicVerifier

__all__ = [
    "BUNDLE_API_VERSION",
    "BUNDLE_KIND",
    "ZERO_HASH",
    "BundleVerificationError",
    "ChainVerification",
    "DuplicateLogEntryError",
    "InMemorySigningKeyStore",
    "InMemoryTransparencyStore",
    "KeyStatus",
    "PostgresSigningKeyStore",
    "PostgresTransparencyStore",
    "PublicVerification",
    "PublicVerifier",
    "SigningKeyRecord",
    "SigningKeyRegistry",
    "SigningKeyStore",
    "TransparencyEntry",
    "TransparencyError",
    "TransparencyLog",
    "TransparencyProof",
    "TransparencyStore",
    "UnknownSigningKeyError",
    "WorkloadBundle",
    "build_signing_key_store",
    "build_transparency_store",
    "compute_bundle_digest",
    "compute_entry_hash",
    "decode_public_key",
    "encode_public_key",
    "export_bundle",
    "import_bundle",
    "sign_bundle",
    "verify_bundle",
]
