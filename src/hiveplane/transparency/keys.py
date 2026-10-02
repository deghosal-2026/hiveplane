"""Signing-key registry, rotation, and public-key distribution (M35-06).

Signing keys persist by ``key_id``; rotation adds a new active key and retires
the previous one while *retaining* its public key, so attestations and bundles
signed under old keys still verify after rotation.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import SigningKeyRow


class KeyStatus(StrEnum):
    """Lifecycle of a signing key."""

    ACTIVE = "active"
    RETIRED = "retired"


class SigningKeyRecord(BaseModel):
    """A distributed public verification key with its lifecycle state."""

    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=1)
    public_key: str = Field(min_length=1)
    status: KeyStatus
    created_at: datetime
    retired_at: datetime | None = None


def encode_public_key(public_key: Ed25519PublicKey) -> str:
    """Return the base64 (raw) encoding used for key distribution."""
    return base64.b64encode(public_key.public_bytes_raw()).decode("ascii")


def decode_public_key(encoded: str) -> Ed25519PublicKey:
    """Decode a base64 (raw) public key produced by :func:`encode_public_key`."""
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded, validate=True))


class SigningKeyStore(Protocol):
    """Storage interface for signing-key records."""

    def save_key(self, record: SigningKeyRecord) -> None: ...

    def get_key(self, key_id: str) -> SigningKeyRecord | None: ...

    def list_keys(self) -> list[SigningKeyRecord]: ...

    def clear(self) -> None: ...


class InMemorySigningKeyStore:
    """A process-local, thread-safe signing-key store."""

    def __init__(self) -> None:
        self._keys: dict[str, SigningKeyRecord] = {}

    def save_key(self, record: SigningKeyRecord) -> None:
        self._keys[record.key_id] = record.model_copy(deep=True)

    def get_key(self, key_id: str) -> SigningKeyRecord | None:
        record = self._keys.get(key_id)
        return None if record is None else record.model_copy(deep=True)

    def list_keys(self) -> list[SigningKeyRecord]:
        records = [record.model_copy(deep=True) for record in self._keys.values()]
        records.sort(key=lambda record: (record.created_at, record.key_id))
        return records

    def clear(self) -> None:
        self._keys.clear()


class PostgresSigningKeyStore:
    """A durable signing-key store backed by PostgreSQL (M35-06)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_key(self, record: SigningKeyRecord) -> None:
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(SigningKeyRow, record.key_id)
            if row is None:
                session.add(
                    SigningKeyRow(
                        key_id=record.key_id,
                        status=record.status.value,
                        created_at=record.created_at,
                        retired_at=record.retired_at,
                        payload=payload,
                    )
                )
            else:
                row.status = record.status.value
                row.retired_at = record.retired_at
                row.payload = payload

    def get_key(self, key_id: str) -> SigningKeyRecord | None:
        with self._session() as session:
            row = session.get(SigningKeyRow, key_id)
            if row is None:
                return None
            return SigningKeyRecord.model_validate(row.payload)

    def list_keys(self) -> list[SigningKeyRecord]:
        statement = select(SigningKeyRow).order_by(
            SigningKeyRow.created_at, SigningKeyRow.key_id
        )
        with self._session() as session:
            return [
                SigningKeyRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(SigningKeyRow))


class SigningKeyRegistry:
    """Coordinates signing-key lifecycle over a store."""

    def __init__(
        self,
        store: SigningKeyStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def add_key(
        self,
        key_id: str,
        public_key: Ed25519PublicKey,
        *,
        status: KeyStatus = KeyStatus.ACTIVE,
    ) -> SigningKeyRecord:
        """Register (or re-register) a public verification key."""
        record = SigningKeyRecord(
            key_id=key_id,
            public_key=encode_public_key(public_key),
            status=status,
            created_at=self._clock(),
        )
        self._store.save_key(record)
        return record

    def rotate(self, key_id: str, public_key: Ed25519PublicKey) -> SigningKeyRecord:
        """Retire the current active key and activate a new one."""
        active_id = self.active_key_id()
        if active_id is not None:
            active = self._store.get_key(active_id)
            if active is not None:
                self._store.save_key(
                    active.model_copy(
                        update={"status": KeyStatus.RETIRED, "retired_at": self._clock()}
                    )
                )
        return self.add_key(key_id, public_key, status=KeyStatus.ACTIVE)

    def active_key_id(self) -> str | None:
        """Return the id of the active key, if any."""
        for key in self._store.list_keys():
            if key.status is KeyStatus.ACTIVE:
                return key.key_id
        return None

    def resolve(self, key_id: str) -> Ed25519PublicKey | None:
        """Resolve a key id to its public key (active or retired)."""
        record = self._store.get_key(key_id)
        if record is None:
            return None
        return decode_public_key(record.public_key)

    def all_keys(self) -> list[SigningKeyRecord]:
        """Return all key records (active and retired)."""
        return self._store.list_keys()


def build_signing_key_store(settings: Settings | None = None) -> SigningKeyStore:
    """Build the configured signing-key store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresSigningKeyStore(create_engine_from_settings(resolved))
    return InMemorySigningKeyStore()
