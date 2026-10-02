"""Models for artifact retention operations (M54)."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PurgeResult(BaseModel):
    """The outcome of a retention purge run."""

    model_config = ConfigDict(extra="forbid")

    purged: list[str] = Field(default_factory=list)
    skipped_legal_hold: list[str] = Field(default_factory=list)
    completed_at: AwareDatetime


class RetentionPolicyNotFoundError(LookupError):
    """Raised when a referenced retention policy does not exist in the tenant."""

    def __init__(self, policy_id: str) -> None:
        super().__init__(f"retention policy not found: {policy_id}")


class RetentionPolicyConflictError(ValueError):
    """Raised when a policy id is already owned by a different tenant."""

    def __init__(self, policy_id: str) -> None:
        super().__init__(f"retention policy {policy_id} already exists for another tenant")
