"""Append-only, hash-chained attestation transparency log (M35-01).

Every certification appends one entry. Each entry's hash covers the previous
hash, its sequence, the attestation id, and the canonical JSON of the signed
attestation:

    entry_hash = sha256(prev_hash ‖ seq ‖ attestation_id ‖ canonical_json(attestation))

so any edit, reorder, or deletion breaks the chain and is detectable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import canonical_payload
from hiveplane.transparency.errors import DuplicateLogEntryError
from hiveplane.transparency.models import (
    ChainVerification,
    TransparencyEntry,
    TransparencyProof,
)
from hiveplane.transparency.store import TransparencyStore

#: ``prev_hash`` of the genesis entry — a sentinel that starts the chain.
ZERO_HASH = "0" * 64


def compute_entry_hash(*, prev_hash: str, seq: int, attestation: Attestation) -> str:
    """Return the hash for a prospective log entry (the documented formula)."""
    material = (
        prev_hash.encode("ascii")
        + str(seq).encode("ascii")
        + attestation.attestation_id.encode("utf-8")
        + canonical_payload(attestation)
    )
    return hashlib.sha256(material).hexdigest()


class TransparencyLog:
    """Coordinates appends, proofs, and integrity checks over a store."""

    def __init__(
        self,
        store: TransparencyStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def append(self, attestation: Attestation) -> TransparencyEntry:
        """Append a signed attestation, linking it to the current chain head."""
        if self._store.get_entry(attestation.attestation_id) is not None:
            raise DuplicateLogEntryError(attestation.attestation_id)
        latest = self._store.latest()
        seq = 0 if latest is None else latest.seq + 1
        prev_hash = ZERO_HASH if latest is None else latest.entry_hash
        entry = TransparencyEntry(
            seq=seq,
            attestation_id=attestation.attestation_id,
            prev_hash=prev_hash,
            entry_hash=compute_entry_hash(
                prev_hash=prev_hash, seq=seq, attestation=attestation
            ),
            created_at=self._clock(),
            attestation=attestation,
        )
        self._store.add_entry(entry)
        return entry

    def get_entry(self, attestation_id: str) -> TransparencyEntry | None:
        """Return a log entry by attestation id, if present."""
        return self._store.get_entry(attestation_id)

    def entries(self) -> list[TransparencyEntry]:
        """Return the full log in sequence order."""
        return self._store.list_entries()

    def latest(self) -> TransparencyEntry | None:
        """Return the current chain head, if any."""
        return self._store.latest()

    def verify_chain(self) -> ChainVerification:
        """Recompute the whole chain and report the first inconsistency."""
        entries = self._store.list_entries()
        prev_hash = ZERO_HASH
        for index, entry in enumerate(entries):
            if entry.seq != index:
                return ChainVerification(
                    valid=False,
                    length=len(entries),
                    tampered_at=index,
                    reason=f"expected sequence {index}, found {entry.seq}",
                )
            if entry.prev_hash != prev_hash:
                return ChainVerification(
                    valid=False,
                    length=len(entries),
                    tampered_at=index,
                    reason="broken hash link to previous entry",
                )
            if entry.attestation_id != entry.attestation.attestation_id:
                return ChainVerification(
                    valid=False,
                    length=len(entries),
                    tampered_at=index,
                    reason="entry attestation id does not match its attestation",
                )
            expected = compute_entry_hash(
                prev_hash=entry.prev_hash, seq=entry.seq, attestation=entry.attestation
            )
            if entry.entry_hash != expected:
                return ChainVerification(
                    valid=False,
                    length=len(entries),
                    tampered_at=index,
                    reason="entry hash does not match its contents",
                )
            prev_hash = entry.entry_hash
        return ChainVerification(valid=True, length=len(entries))

    def prove(self, attestation_id: str) -> TransparencyProof | None:
        """Return an inclusion proof for an attestation, or ``None`` if absent."""
        entry = self._store.get_entry(attestation_id)
        if entry is None:
            return None
        verification = self.verify_chain()
        return TransparencyProof(
            attestation_id=attestation_id,
            entry=entry,
            valid=verification.valid,
            chain_length=verification.length,
        )
