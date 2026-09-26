"""Secret store models: versioned refs, ciphertext, metadata, injection (M45)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class SecretError(Exception):
    """Base class for secret-store failures."""


class SecretNotFoundError(SecretError):
    """Raised when a secret or version does not exist."""

    def __init__(self, ref: str) -> None:
        super().__init__(f"secret {ref!r} not found")
        self.ref = ref


class SecretResolutionError(SecretError):
    """Raised when a secret cannot be resolved (fail-closed at the boundary)."""

    def __init__(self, ref: str, reason: str) -> None:
        super().__init__(f"cannot resolve {ref!r}: {reason}")
        self.ref = ref
        self.reason = reason


class SecretLeakError(SecretError):
    """Raised when a cleared secret value is found in a sink (fail-closed)."""

    def __init__(self, sink: str) -> None:
        super().__init__(f"secret value detected in sink {sink!r}")
        self.sink = sink


class SecretInjectionKind(StrEnum):
    """How a resolved secret is delivered to a run."""

    ENV = "env"
    FILE = "file"


class SecretInjection(BaseModel):
    """Where and how to inject a resolved secret."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    injection_kind: SecretInjectionKind = Field(alias="as")
    name: str | None = None
    path: str | None = None
    mode: str = "0400"


class SecretRef(BaseModel):
    """A versioned reference: ``secret://<tenant>/<name>@<version>``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    version: int | None = Field(default=None, ge=1)

    @classmethod
    def parse(cls, text: str) -> SecretRef:
        """Parse a secret URI, raising :class:`SecretError` when malformed."""
        if not text.startswith("secret://"):
            raise SecretError(f"invalid secret ref {text!r}")
        body = text[len("secret://") :]
        if "/" not in body:
            raise SecretError(f"invalid secret ref {text!r}")
        tenant, _, rest = body.partition("/")
        name, _, version_text = rest.partition("@")
        if not tenant or not name:
            raise SecretError(f"invalid secret ref {text!r}")
        version = None
        if version_text:
            if not version_text.isdigit() or int(version_text) < 1:
                raise SecretError(f"invalid secret version in {text!r}")
            version = int(version_text)
        return cls(tenant_id=tenant, name=name, version=version)

    def render(self) -> str:
        """Render the canonical URI."""
        suffix = f"@{self.version}" if self.version is not None else ""
        return f"secret://{self.tenant_id}/{self.name}{suffix}"


class SecretVersion(BaseModel):
    """A single encrypted version of a secret (ciphertext only)."""

    model_config = ConfigDict(extra="forbid")

    secret_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    ciphertext: bytes
    nonce: bytes
    wrapped_key: bytes
    key_nonce: bytes
    key_id: str = Field(min_length=1)
    created_at: AwareDatetime
    revoked_at: AwareDatetime | None = None


class SecretRecord(BaseModel):
    """Secret metadata plus its append-only versions (never plaintext)."""

    model_config = ConfigDict(extra="forbid")

    secret_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    current_version: int = Field(ge=1)
    created_at: AwareDatetime
    rotated_at: AwareDatetime | None = None
    versions: list[int] = Field(default_factory=list)


class SecretMetadata(BaseModel):
    """Public metadata view returned to operators and the UI."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    current_version: int = Field(ge=1)
    versions: list[int] = Field(default_factory=list)
    created_at: AwareDatetime
    rotated_at: AwareDatetime | None = None
    consumers: list[str] = Field(default_factory=list)


class ResolvedSecret(BaseModel):
    """A transient plaintext secret resolved at the execution boundary."""

    model_config = ConfigDict(extra="forbid")

    ref: SecretRef
    value: str = Field(repr=False)
    injection: SecretInjection | None = None
