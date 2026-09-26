"""Secret service: create, rotate, resolve by versioned ref (M45-01/04)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from hiveplane.secrets.crypto import KeyProvider
from hiveplane.secrets.models import (
    ResolvedSecret,
    SecretMetadata,
    SecretNotFoundError,
    SecretRecord,
    SecretRef,
    SecretResolutionError,
    SecretVersion,
)
from hiveplane.secrets.store import SecretStore


def new_secret_id() -> str:
    """Return a fresh opaque secret id."""
    return f"sec-{uuid4().hex[:20]}"


class SecretAlreadyExistsError(Exception):
    """Raised when creating a secret that already exists."""

    def __init__(self, tenant_id: str, name: str) -> None:
        super().__init__(f"secret {tenant_id}/{name} already exists")
        self.tenant_id = tenant_id
        self.name = name


class SecretService:
    """Manages encrypted secrets and resolves them at the boundary."""

    def __init__(
        self,
        store: SecretStore,
        provider: KeyProvider,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] = new_secret_id,
    ) -> None:
        self._store = store
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._consumers: dict[tuple[str, str], set[str]] = {}

    def put(self, tenant_id: str, name: str, value: str) -> SecretRecord:
        """Create a secret's first version (fail-closed if it exists)."""
        if self._store.get_secret(tenant_id, name) is not None:
            raise SecretAlreadyExistsError(tenant_id, name)
        secret_id = self._id_factory()
        now = self._clock()
        self._encrypt_and_store(secret_id, tenant_id, name, 1, value, now)
        record = SecretRecord(
            secret_id=secret_id,
            tenant_id=tenant_id,
            name=name,
            current_version=1,
            created_at=now,
            versions=[1],
        )
        self._store.save_secret(record)
        return record

    def rotate(self, tenant_id: str, name: str, value: str) -> SecretRecord:
        """Append a new current version; in-flight pins keep their version."""
        record = self._require(tenant_id, name)
        now = self._clock()
        version = record.current_version + 1
        self._encrypt_and_store(record.secret_id, tenant_id, name, version, value, now)
        record.current_version = version
        record.rotated_at = now
        record.versions = [*record.versions, version]
        self._store.save_secret(record)
        return record

    def metadata(self, tenant_id: str, name: str) -> SecretMetadata:
        """Return public metadata (never plaintext)."""
        record = self._require(tenant_id, name)
        return self._to_metadata(record)

    def list_secrets(self, tenant_id: str) -> list[SecretMetadata]:
        """List a tenant's secret metadata."""
        return [
            self._to_metadata(record) for record in self._store.list_secrets(tenant_id)
        ]

    def resolve(
        self,
        ref: SecretRef,
        *,
        run_id: str | None = None,
        workload: str | None = None,
    ) -> ResolvedSecret:
        """Resolve a ref to plaintext at the boundary (fail-closed)."""
        record = self._store.get_secret(ref.tenant_id, ref.name)
        if record is None:
            raise SecretResolutionError(ref.render(), "unknown secret")
        version = ref.version if ref.version is not None else record.current_version
        stored = self._store.get_version(record.secret_id, version)
        if stored is None:
            raise SecretResolutionError(ref.render(), "unknown version")
        if stored.revoked_at is not None:
            raise SecretResolutionError(ref.render(), "version revoked")
        plaintext = self._provider.decrypt(
            ciphertext=stored.ciphertext,
            nonce=stored.nonce,
            wrapped_key=stored.wrapped_key,
            key_nonce=stored.key_nonce,
            key_id=stored.key_id,
        )
        if workload is not None:
            self._consumers.setdefault((ref.tenant_id, ref.name), set()).add(workload)
        return ResolvedSecret(
            ref=SecretRef(tenant_id=ref.tenant_id, name=ref.name, version=version),
            value=plaintext.decode("utf-8"),
        )

    def revoke_version(self, tenant_id: str, name: str, version: int) -> None:
        """Revoke a specific version (no fallback to older values)."""
        record = self._require(tenant_id, name)
        stored = self._store.get_version(record.secret_id, version)
        if stored is None:
            raise SecretNotFoundError(f"{tenant_id}/{name}@{version}")
        stored.revoked_at = self._clock()
        self._store.save_version(stored)

    def consumers(self, tenant_id: str, name: str) -> list[str]:
        """Return the workloads that have resolved this secret."""
        return sorted(self._consumers.get((tenant_id, name), set()))

    def _require(self, tenant_id: str, name: str) -> SecretRecord:
        record = self._store.get_secret(tenant_id, name)
        if record is None:
            raise SecretNotFoundError(f"{tenant_id}/{name}")
        return record

    def _encrypt_and_store(
        self,
        secret_id: str,
        tenant_id: str,
        name: str,
        version: int,
        value: str,
        now: datetime,
    ) -> None:
        encrypted = self._provider.encrypt(value.encode("utf-8"))
        self._store.save_version(
            SecretVersion(
                secret_id=secret_id,
                tenant_id=tenant_id,
                name=name,
                version=version,
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
                wrapped_key=encrypted.wrapped_key,
                key_nonce=encrypted.key_nonce,
                key_id=encrypted.key_id,
                created_at=now,
            )
        )

    def _to_metadata(self, record: SecretRecord) -> SecretMetadata:
        return SecretMetadata(
            tenant_id=record.tenant_id,
            name=record.name,
            current_version=record.current_version,
            versions=list(record.versions),
            created_at=record.created_at,
            rotated_at=record.rotated_at,
            consumers=self.consumers(record.tenant_id, record.name),
        )
