"""Typed records for the attestation transparency log (M35-01)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from hiveplane.certification.models import Attestation


class TransparencyEntry(BaseModel):
    """One append-only, hash-chained log entry for a certification.

    The entry carries the full signed attestation so the chain is independently
    verifiable without a second lookup, and tampering with either the attestation
    or the stored hash is detectable.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int
    attestation_id: str
    prev_hash: str
    entry_hash: str
    created_at: datetime
    attestation: Attestation

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class ChainVerification(BaseModel):
    """The result of verifying the integrity of the transparency log."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    length: int
    tampered_at: int | None = None
    reason: str | None = None


class TransparencyProof(BaseModel):
    """An inclusion proof that an attestation is in a valid chain."""

    model_config = ConfigDict(extra="forbid")

    attestation_id: str
    entry: TransparencyEntry
    valid: bool
    chain_length: int
