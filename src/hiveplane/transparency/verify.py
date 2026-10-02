"""Public (unauthenticated) attestation verification (M35-02).

Returns only public evidence — validity, the signer's key id, issue time,
status, and inclusion in the hash-chained transparency log. It never exposes a
workload's prompt, corpus, model inputs, or any secret.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict

from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import verify_attestation
from hiveplane.transparency.log import TransparencyLog

KeyResolver = Callable[[str], Ed25519PublicKey | None]


class PublicVerification(BaseModel):
    """The public evidence returned for an attestation verification request."""

    model_config = ConfigDict(extra="forbid")

    attestation_id: str
    valid: bool
    signer_key_id: str | None = None
    issued_at: datetime | None = None
    status: str | None = None
    target_context: str | None = None
    log_seq: int | None = None
    chain_valid: bool = False
    chain_length: int = 0
    reason: str | None = None


class PublicVerifier:
    """Verifies an attestation's signature and its transparency-log inclusion."""

    def __init__(
        self,
        *,
        get_attestation: Callable[[str], Attestation | None],
        log: TransparencyLog,
        key_resolver: KeyResolver,
    ) -> None:
        self._get_attestation = get_attestation
        self._log = log
        self._key_resolver = key_resolver

    def verify(self, attestation_id: str) -> PublicVerification | None:
        """Return public evidence, or ``None`` when the attestation is unknown."""
        attestation = self._get_attestation(attestation_id)
        if attestation is None:
            return None
        entry = self._log.get_entry(attestation_id)
        chain = self._log.verify_chain()
        public_key = self._key_resolver(attestation.signer.key_id)
        signature_valid = public_key is not None and verify_attestation(
            attestation, public_key
        )

        reason: str | None = None
        if not signature_valid:
            reason = "attestation signature could not be verified"
        elif entry is None:
            reason = "attestation is not present in the transparency log"
        elif not chain.valid:
            reason = "transparency log chain verification failed"

        return PublicVerification(
            attestation_id=attestation_id,
            valid=signature_valid and entry is not None and chain.valid,
            signer_key_id=attestation.signer.key_id,
            issued_at=attestation.timestamp,
            status=attestation.status.value,
            target_context=attestation.target_context.value,
            log_seq=None if entry is None else entry.seq,
            chain_valid=chain.valid,
            chain_length=chain.length,
            reason=reason,
        )
