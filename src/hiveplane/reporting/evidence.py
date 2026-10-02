"""Signed, self-contained compliance evidence packs (M57-04)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from hiveplane.certification.binding import canonical_json
from hiveplane.certification.signing import generate_keypair
from hiveplane.certification.store import CertificationStore
from hiveplane.cost.store import CostStore
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.persistence.audit import AuditLog
from hiveplane.policy.approvals import ApprovalService
from hiveplane.reporting.errors import EvidencePackNotFoundError
from hiveplane.reporting.models import EvidenceFile, EvidencePack, PackSignature
from hiveplane.reporting.store import ReportingStore
from hiveplane.tenancy.context import TenantContext

_DOMAIN = b"hiveplane/reporting/evidence/v1|"
_FILE_NAMES = ("index.json", "approvals.json", "attestations.json", "spend.json")


class EvidencePackService:
    """Assembles, signs, persists, and verifies compliance evidence packs."""

    def __init__(
        self,
        reporting_store: ReportingStore,
        approval_service: ApprovalService,
        certification_store: CertificationStore,
        cost_store: CostStore,
        *,
        private_key: Ed25519PrivateKey | None = None,
        key_id: str = "reporting",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reporting_store = reporting_store
        self._approval_service = approval_service
        self._certification_store = certification_store
        self._cost_store = cost_store
        if private_key is None:
            private_key, public_key = generate_keypair()
        else:
            public_key = private_key.public_key()
        self._private_key = private_key
        self._public_key = public_key
        self._key_id = key_id
        self._audit: AuditLog | None = None
        self._clock = clock or (lambda: datetime.now(UTC))

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def public_key(self) -> Ed25519PublicKey:
        """Return the public key that verifies this service's packs."""
        return self._public_key

    def public_key_pem(self) -> str:
        """Return the public key as a PEM document for offline verification."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def generate(
        self,
        ctx: TenantContext,
        *,
        period_start: datetime,
        period_end: datetime,
    ) -> EvidencePack:
        """Assemble, sign, persist, and audit a pack for the acting tenant."""
        tenant_id = ctx.tenant_id
        kind = _period_kind(period_start, period_end)
        approvals = sorted(
            (
                record
                for record in self._approval_service.list(ctx=ctx)
                if period_start <= record.requested_at <= period_end
            ),
            key=lambda record: (record.requested_at, record.approval_id),
        )
        attestations = sorted(
            (
                record
                for record in self._certification_store.list(ctx=ctx)
                if period_start <= record.certification.timestamp <= period_end
            ),
            key=lambda record: (record.certification.timestamp, record.record_id),
        )
        spend_events = sorted(
            self._cost_store.list_events(
                tenant_id,
                ctx=ctx,
                start=period_start,
                end=period_end + timedelta(microseconds=1),
            ),
            key=lambda event: (event.occurred_at, event.event_id),
        )

        approvals_file = _evidence_file(
            "approvals.json", [record.model_dump(mode="json") for record in approvals]
        )
        attestations_file = _evidence_file(
            "attestations.json",
            [record.model_dump(mode="json") for record in attestations],
        )
        spend_file = _evidence_file(
            "spend.json",
            {
                "tenant_id": tenant_id,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "event_count": len(spend_events),
                "total_cost_usd": sum(event.cost_usd for event in spend_events),
                "events": [event.model_dump(mode="json") for event in spend_events],
            },
        )
        index_file = _evidence_file(
            "index.json",
            {
                "tenant_id": tenant_id,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "period_kind": kind.value,
                "counts": {
                    "approvals": len(approvals),
                    "attestations": len(attestations),
                    "spend_events": len(spend_events),
                },
                "files": {
                    "approvals.json": approvals_file.digest,
                    "attestations.json": attestations_file.digest,
                    "spend.json": spend_file.digest,
                },
            },
        )
        files = [index_file, approvals_file, attestations_file, spend_file]
        created_at = self._clock()
        pack = EvidencePack(
            pack_id=_pack_id(tenant_id, period_start, period_end, files),
            tenant_id=tenant_id,
            period_start=period_start,
            period_end=period_end,
            files=files,
            signature=_placeholder_signature(self._key_id),
            created_at=created_at,
        )
        payload = _signing_payload(pack)
        signed = self._private_key.sign(payload)
        pack = pack.model_copy(
            update={
                "signature": PackSignature(
                    key_id=self._key_id,
                    algorithm="ed25519",
                    digest=hashlib.sha256(payload).hexdigest(),
                    signature=base64.b64encode(signed).decode("ascii"),
                )
            }
        )
        self._reporting_store.save_evidence(pack, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                ctx.operator_id or "operator",
                "compliance.evidence_pack.generated",
                pack.pack_id,
                detail=f"tenant={tenant_id} files={len(pack.files)}",
                ctx=ctx,
            )
        return pack

    def verify(self, pack: EvidencePack, public_key: Ed25519PublicKey) -> bool:
        """Return True when the pack's digest, signature, and files are intact."""
        if pack.signature.algorithm != "ed25519":
            return False
        try:
            payload = _signing_payload(pack)
            if hashlib.sha256(payload).hexdigest() != pack.signature.digest:
                return False
            signature = base64.b64decode(pack.signature.signature, validate=True)
            public_key.verify(signature, payload)
        except (InvalidSignature, ValueError, TypeError, binascii.Error):
            return False
        return _files_consistent(pack)

    def list(self, ctx: TenantContext) -> list[EvidencePack]:
        """List the acting tenant's persisted evidence packs."""
        return self._reporting_store.list_evidence(ctx=ctx)

    def get(self, pack_id: str, *, ctx: TenantContext) -> EvidencePack:
        """Return one pack in the acting tenant, or raise when missing."""
        pack = self._reporting_store.get_evidence(pack_id, tenant_id=ctx.tenant_id, ctx=ctx)
        if pack is None:
            raise EvidencePackNotFoundError(pack_id)
        return pack


def _period_kind(period_start: datetime, period_end: datetime) -> CostPeriodKind:
    span = period_end - period_start
    if span <= timedelta(days=1):
        return CostPeriodKind.DAY
    if span <= timedelta(days=7):
        return CostPeriodKind.WEEK
    return CostPeriodKind.MONTH


def _evidence_file(name: str, payload: Any) -> EvidenceFile:
    content = canonical_json(payload)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return EvidenceFile(
        name=name, media_type="application/json", digest=digest, content=content
    )


def _placeholder_signature(key_id: str) -> PackSignature:
    return PackSignature(
        key_id=key_id,
        algorithm="ed25519",
        digest="0" * 64,
        signature=base64.b64encode(b"\x00" * 64).decode("ascii"),
    )


def _signing_payload(pack: EvidencePack) -> bytes:
    document = pack.model_dump(mode="json", exclude={"signature"})
    return _DOMAIN + canonical_json(document).encode("utf-8")


def _files_consistent(pack: EvidencePack) -> bool:
    for item in pack.files:
        if hashlib.sha256(item.content.encode("utf-8")).hexdigest() != item.digest:
            return False
    by_name = {item.name: item for item in pack.files}
    index = by_name.get("index.json")
    if index is None:
        return False
    try:
        manifest = json.loads(index.content)
    except json.JSONDecodeError:
        return False
    if not isinstance(manifest, dict):
        return False
    expected = {
        item.name: item.digest
        for item in pack.files
        if item.name != "index.json"
    }
    return manifest.get("files") == expected


def _pack_id(
    tenant_id: str,
    period_start: datetime,
    period_end: datetime,
    files: list[EvidenceFile],
) -> str:
    payload = "|".join(
        [
            tenant_id,
            period_start.isoformat(),
            period_end.isoformat(),
            *(item.digest for item in files),
        ]
    )
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"pack-{token}"
