"""Control-plane backup and restore with a signed integrity manifest (M59-03)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from hiveplane import __version__
from hiveplane.backup.errors import BackupIntegrityError, BackupSchemaMismatchError
from hiveplane.backup.models import (
    SCHEMA_HEAD,
    BackupArchive,
    BackupFile,
    BackupManifest,
    RestoreCount,
)
from hiveplane.certification.signing import generate_keypair

_DOMAIN = b"hiveplane/backup/v1|"


class BackupTarget(Protocol):
    """A named slice of control-plane state that can be exported and restored."""

    @property
    def name(self) -> str: ...

    def export(self) -> list[dict[str, object]]: ...

    def restore(self, rows: list[dict[str, object]]) -> int: ...


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BackupService:
    """Creates, verifies, and restores signed control-plane backups."""

    def __init__(
        self,
        targets: Sequence[BackupTarget],
        *,
        private_key: Ed25519PrivateKey | None = None,
        key_id: str = "backup",
        clock: Callable[[], datetime] | None = None,
        schema_head: str = SCHEMA_HEAD,
    ) -> None:
        self._targets = list(targets)
        if private_key is None:
            private_key, public_key = generate_keypair()
        else:
            public_key = private_key.public_key()
        self._private_key = private_key
        self._public_key = public_key
        self._key_id = key_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._schema_head = schema_head

    def public_key(self) -> Ed25519PublicKey:
        """Return the public key that verifies this service's archives."""
        return self._public_key

    def _manifest_bytes(self, manifest: BackupManifest) -> bytes:
        return _DOMAIN + _canonical(manifest.model_dump(mode="json")).encode("utf-8")

    def create(self) -> BackupArchive:
        """Snapshot every target into a signed, integrity-checked archive."""
        payloads: dict[str, str] = {}
        files: list[BackupFile] = []
        for target in self._targets:
            rows = target.export()
            payload = _canonical({"target": target.name, "rows": rows})
            payloads[target.name] = payload
            files.append(BackupFile(name=target.name, sha256=_digest(payload), records=len(rows)))
        manifest = BackupManifest(
            backup_id=f"backup-{uuid4().hex[:12]}",
            created_at=self._clock(),
            hiveplane_version=__version__,
            alembic_head=self._schema_head,
            files=files,
            total_records=sum(entry.records for entry in files),
        )
        signature = self._private_key.sign(self._manifest_bytes(manifest)).hex()
        return BackupArchive(
            manifest=manifest, payloads=payloads, key_id=self._key_id, signature=signature
        )

    def verify(self, archive: BackupArchive, public_key: Ed25519PublicKey) -> bool:
        """Recompute every payload digest and check the manifest signature."""
        try:
            public_key.verify(
                bytes.fromhex(archive.signature), self._manifest_bytes(archive.manifest)
            )
        except Exception:
            return False
        for entry in archive.manifest.files:
            payload = archive.payloads.get(entry.name)
            if payload is None or _digest(payload) != entry.sha256:
                return False
        return True

    def restore(
        self,
        archive: BackupArchive,
        public_key: Ed25519PublicKey,
        *,
        expected_head: str | None = None,
    ) -> list[RestoreCount]:
        """Verify then restore every target; refuse a schema mismatch."""
        if not self.verify(archive, public_key):
            raise BackupIntegrityError("backup failed integrity or signature verification")
        head = expected_head or self._schema_head
        if archive.manifest.alembic_head != head:
            raise BackupSchemaMismatchError(head, archive.manifest.alembic_head)
        counts: list[RestoreCount] = []
        for target in self._targets:
            payload = archive.payloads.get(target.name)
            if payload is None:
                continue
            rows = json.loads(payload)["rows"]
            counts.append(RestoreCount(target=target.name, restored=target.restore(rows)))
        return counts
