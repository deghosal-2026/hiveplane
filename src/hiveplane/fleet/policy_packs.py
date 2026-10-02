"""Versioned policy packs and policy decision records (M25-04, D21/D29).

A policy pack version is a versioned, inheritable bundle with a lint status and
a content hash. Every evaluated policy decision is recorded with its outcome,
reason, and the id of the originating rule, so "why was this denied?" is
answerable without re-deriving.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from hiveplane.core.decision import ActionClass, DecisionOutcome
from hiveplane.policy.models import PolicyPackSpec
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class LintStatus(StrEnum):
    """The result of linting a policy pack version."""

    CLEAN = "clean"
    WARNINGS = "warnings"
    ERRORS = "errors"


class PolicyPackVersion(BaseModel):
    """A versioned, inheritable policy pack."""

    model_config = ConfigDict(extra="forbid")

    pack_version_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    version: int = Field(ge=1)
    parent_pack_id: str | None = Field(default=None, max_length=64)
    lint_status: LintStatus
    content_hash: str = Field(min_length=1, max_length=128)
    spec: PolicyPackSpec
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _parent_must_differ(self) -> Self:
        if self.parent_pack_id is not None and self.parent_pack_id == self.pack_version_id:
            raise ValueError("a policy pack version cannot inherit from itself")
        return self


class PolicyDecisionRecord(BaseModel):
    """An append-only record explaining one evaluated policy decision."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    workload: str = Field(min_length=1, max_length=253)
    outcome: DecisionOutcome
    reason: str = Field(min_length=1, max_length=2000)
    originating_rule_id: str | None = Field(default=None, max_length=253)
    action_class: ActionClass | None = None
    policy_pack_version_id: str | None = Field(default=None, max_length=64)
    evaluated_at: AwareDatetime
