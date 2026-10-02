"""Encrypted per-tenant secret store, rotation, injection, and redaction (M45)."""

from __future__ import annotations

from hiveplane.secrets.crypto import (
    EncryptedValue,
    KeyProvider,
    LocalKeyProvider,
    load_or_create_master_key,
)
from hiveplane.secrets.injection import InjectedSecret, SecretInjector
from hiveplane.secrets.models import (
    ResolvedSecret,
    SecretError,
    SecretInjection,
    SecretInjectionKind,
    SecretLeakError,
    SecretMetadata,
    SecretNotFoundError,
    SecretRecord,
    SecretRef,
    SecretResolutionError,
    SecretVersion,
)
from hiveplane.secrets.redaction import (
    RedactionLogFilter,
    Redactor,
    RedactorRegistry,
)
from hiveplane.secrets.service import (
    SecretAlreadyExistsError,
    SecretService,
    new_secret_id,
)
from hiveplane.secrets.store import (
    InMemorySecretStore,
    PostgresSecretStore,
    SecretStore,
    build_secret_store,
)

__all__ = [
    "EncryptedValue",
    "InMemorySecretStore",
    "InjectedSecret",
    "KeyProvider",
    "LocalKeyProvider",
    "PostgresSecretStore",
    "RedactionLogFilter",
    "Redactor",
    "RedactorRegistry",
    "ResolvedSecret",
    "SecretAlreadyExistsError",
    "SecretError",
    "SecretInjection",
    "SecretInjectionKind",
    "SecretInjector",
    "SecretLeakError",
    "SecretMetadata",
    "SecretNotFoundError",
    "SecretRecord",
    "SecretRef",
    "SecretResolutionError",
    "SecretService",
    "SecretStore",
    "SecretVersion",
    "build_secret_store",
    "load_or_create_master_key",
    "new_secret_id",
]
