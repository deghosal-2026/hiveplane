"""Artifact and retention models (M25-07, D21/D38).

An artifact is metadata only: its run linkage, location (local path or
``s3://``/``minio://`` URI), size, content hash, retention policy, and expiry.
Bytes live in the configured backend. Retention policies are per tenant.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from hiveplane.tenancy.context import DEFAULT_TENANT_ID

_LOCATION_SCHEMES = ("s3://", "minio://", "file://")


class RetentionPolicy(BaseModel):
    """A per-tenant retention rule for a data class."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    data_class: str = Field(min_length=1, max_length=128)
    retain_days: int = Field(ge=0)
    legal_hold: bool = False


class Artifact(BaseModel):
    """Metadata for a stored execution artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    location: str = Field(min_length=1, max_length=2048)
    size_bytes: int = Field(ge=0)
    content_hash: str = Field(min_length=1, max_length=128)
    retention_policy_id: str | None = Field(default=None, max_length=64)
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None

    @field_validator("location")
    @classmethod
    def _location_is_path_or_uri(cls, value: str) -> str:
        if "://" in value and not value.startswith(_LOCATION_SCHEMES):
            raise ValueError(
                "artifact location must be a local path or one of "
                f"{', '.join(_LOCATION_SCHEMES)}"
            )
        return value
