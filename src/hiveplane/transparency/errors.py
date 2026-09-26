"""Transparency-domain errors (M35)."""

from __future__ import annotations


class TransparencyError(Exception):
    """Base class for transparency log and provenance errors."""


class DuplicateLogEntryError(TransparencyError):
    """Raised when appending an attestation that is already in the log."""

    def __init__(self, attestation_id: str) -> None:
        super().__init__(
            f"attestation {attestation_id!r} is already in the transparency log (append-only)"
        )
        self.attestation_id = attestation_id


class UnknownSigningKeyError(TransparencyError):
    """Raised when a signing key id cannot be resolved to a public key."""

    def __init__(self, key_id: str) -> None:
        super().__init__(f"signing key {key_id!r} is not registered")
        self.key_id = key_id


class BundleVerificationError(TransparencyError):
    """Raised when a workload bundle signature or digest does not verify."""

    def __init__(self, workload_id: str, reason: str) -> None:
        super().__init__(f"workload bundle {workload_id!r} failed verification: {reason}")
        self.workload_id = workload_id
        self.reason = reason
