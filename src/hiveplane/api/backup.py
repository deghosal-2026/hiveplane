"""Control-plane backup and restore API (M59-03)."""

from __future__ import annotations

from typing import Annotated, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from hiveplane.api.deps import get_backup_service, require_permission
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.backup.errors import BackupError
from hiveplane.backup.models import BackupArchive, RestoreCount
from hiveplane.backup.service import BackupService

router = APIRouter(tags=["backup"])

BackupDep = Annotated[BackupService, Depends(get_backup_service)]
BackupAdmin = Annotated[OperatorIdentity, Depends(require_permission(Permission.KEYS_MANAGE))]


class VerifyRequest(BaseModel):
    """A backup archive plus the public key expected to verify it."""

    model_config = ConfigDict(extra="forbid")

    archive: BackupArchive
    public_key_pem: str


class VerifyResponse(BaseModel):
    """The result of verifying a backup archive."""

    model_config = ConfigDict(extra="forbid")

    valid: bool


def _load_public_key(pem: str) -> Ed25519PublicKey:
    try:
        key = load_pem_public_key(pem.encode("ascii"))
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid public key") from exc
    return cast(Ed25519PublicKey, key)


def _same_key(a: Ed25519PublicKey, b: Ed25519PublicKey) -> bool:
    raw = serialization.Encoding.Raw
    fmt = serialization.PublicFormat.Raw
    return a.public_bytes(raw, fmt) == b.public_bytes(raw, fmt)


def _plane_key(service: BackupService, pem: str) -> Ed25519PublicKey:
    """Return the plane's verification key; refuse a caller-supplied foreign key.

    The archive signature only authenticates provenance when checked against a
    key the plane already trusts (its configured backup key), so an arbitrary
    key from the request body must never be used to verify a restore (D38).
    """
    supplied = _load_public_key(pem)
    plane = service.public_key()
    if not _same_key(supplied, plane):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "public key does not match this plane"
        )
    return plane


@router.get("/backup/public-key")
def backup_public_key(
    service: BackupDep, _: BackupAdmin
) -> dict[str, str]:
    """Return the PEM public key that verifies this plane's backups."""
    pem = service.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {"public_key_pem": pem.decode("ascii")}


@router.post("/backup", response_model=BackupArchive)
def create_backup(service: BackupDep, _: BackupAdmin) -> BackupArchive:
    """Snapshot control-plane state into a signed archive."""
    return service.create()


@router.post("/backup/verify", response_model=VerifyResponse)
def verify_backup(
    request: VerifyRequest, service: BackupDep, _: BackupAdmin
) -> VerifyResponse:
    """Verify an archive's integrity and signature without restoring it."""
    public_key = _plane_key(service, request.public_key_pem)
    return VerifyResponse(valid=service.verify(request.archive, public_key))


@router.post("/backup/restore", response_model=list[RestoreCount])
def restore_backup(
    request: VerifyRequest, service: BackupDep, _: BackupAdmin
) -> list[RestoreCount]:
    """Restore control-plane state from a verified archive."""
    public_key = _plane_key(service, request.public_key_pem)
    try:
        return service.restore(request.archive, public_key)
    except BackupError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
