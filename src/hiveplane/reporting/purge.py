"""Tenant purge and signed purge certificates (M57-07, D38)."""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from hiveplane.certification.binding import canonical_json
from hiveplane.certification.signing import generate_keypair
from hiveplane.persistence.audit import AuditLog
from hiveplane.reporting.errors import PurgeIncompleteError
from hiveplane.reporting.models import (
    PackSignature,
    PurgeCertificate,
    PurgeRecord,
    StorePurgeCount,
)
from hiveplane.reporting.store import ReportingStore
from hiveplane.tenancy.context import TenantContext

_DOMAIN = b"hiveplane/reporting/purge/v1|"


@dataclass(frozen=True, slots=True)
class PurgeTarget:
    """One store's tenant purge callable and the name recorded for it."""

    name: str
    purge: Callable[[str], int]


PurgeTargets = Sequence[PurgeTarget]


class TenantPurgeService:
    """Purges a tenant across every store and issues a signed certificate."""

    def __init__(
        self,
        reporting_store: ReportingStore,
        targets: PurgeTargets,
        *,
        legal_hold_check: Callable[[str], bool],
        signing_key: Ed25519PrivateKey | None = None,
        key_id: str = "reporting",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reporting_store = reporting_store
        self._targets = list(targets)
        self._legal_hold_check = legal_hold_check
        if signing_key is None:
            signing_key, public_key = generate_keypair()
        else:
            public_key = signing_key.public_key()
        self._private_key = signing_key
        self._public_key = public_key
        self._key_id = key_id
        self._audit: AuditLog | None = None
        self._clock = clock or (lambda: datetime.now(UTC))

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def public_key(self) -> Ed25519PublicKey:
        """Return the public key that verifies this service's certificates."""
        return self._public_key

    def public_key_pem(self) -> str:
        """Return the public key as a PEM document for offline verification."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def purge(self, ctx: TenantContext, tenant_id: str, *, actor: str = "operator") -> PurgeRecord:
        """Delete every target store's tenant data and certify completion."""
        if self._legal_hold_check(tenant_id):
            raise PurgeIncompleteError(tenant_id, "legal hold is active")
        counts = [
            StorePurgeCount(store=target.name, deleted=target.purge(tenant_id))
            for target in self._targets
        ]
        completed_at = self._clock()
        purge_id = _purge_id(tenant_id, completed_at, counts)
        certificate = PurgeCertificate(
            purge_id=purge_id,
            tenant_id=tenant_id,
            scope="tenant",
            stores=counts,
            total_deleted=sum(count.deleted for count in counts),
            completed_at=completed_at,
            verifier=actor,
            signature=_placeholder_signature(self._key_id),
        )
        payload = _signing_payload(certificate)
        signed = self._private_key.sign(payload)
        certificate = certificate.model_copy(
            update={
                "signature": PackSignature(
                    key_id=self._key_id,
                    algorithm="ed25519",
                    digest=hashlib.sha256(payload).hexdigest(),
                    signature=base64.b64encode(signed).decode("ascii"),
                )
            }
        )
        record = PurgeRecord(
            purge_id=purge_id,
            tenant_id=tenant_id,
            scope="tenant",
            certificate=certificate,
            completed_at=completed_at,
        )
        self._reporting_store.save_purge(record, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                actor,
                "tenant.purged",
                purge_id,
                detail=f"tenant={tenant_id} deleted={certificate.total_deleted}",
                ctx=ctx,
            )
        return record

    def verify(self, record: PurgeRecord, public_key: Ed25519PublicKey) -> bool:
        """Return True when a record's certificate is intact and internally consistent."""
        certificate = record.certificate
        if certificate.signature.algorithm != "ed25519":
            return False
        if (
            record.purge_id != certificate.purge_id
            or record.tenant_id != certificate.tenant_id
            or record.scope != certificate.scope
            or record.completed_at != certificate.completed_at
        ):
            return False
        if certificate.total_deleted != sum(count.deleted for count in certificate.stores):
            return False
        try:
            payload = _signing_payload(certificate)
            if hashlib.sha256(payload).hexdigest() != certificate.signature.digest:
                return False
            signature = base64.b64decode(certificate.signature.signature, validate=True)
            public_key.verify(signature, payload)
        except (InvalidSignature, ValueError, TypeError, binascii.Error):
            return False
        return True

    def list(self, ctx: TenantContext) -> list[PurgeRecord]:
        """List the acting tenant's persisted purge records."""
        return self._reporting_store.list_purges(ctx=ctx)


def _placeholder_signature(key_id: str) -> PackSignature:
    return PackSignature(
        key_id=key_id,
        algorithm="ed25519",
        digest="0" * 64,
        signature=base64.b64encode(b"\x00" * 64).decode("ascii"),
    )


def _signing_payload(certificate: PurgeCertificate) -> bytes:
    document = certificate.model_dump(mode="json", exclude={"signature"})
    return _DOMAIN + canonical_json(document).encode("utf-8")


def _purge_id(tenant_id: str, completed_at: datetime, counts: list[StorePurgeCount]) -> str:
    payload = "|".join(
        [
            tenant_id,
            completed_at.isoformat(),
            *(f"{count.store}={count.deleted}" for count in counts),
        ]
    )
    return f"purge-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"
