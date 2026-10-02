"""Models for published, immutable corpus versions (M55-03)."""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class CorpusRelease(BaseModel):
    """An immutable, content-hashed published corpus version."""

    model_config = ConfigDict(extra="forbid")

    corpus_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    content_hash: str = Field(min_length=1, max_length=128)
    corpus: dict[str, Any]
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
