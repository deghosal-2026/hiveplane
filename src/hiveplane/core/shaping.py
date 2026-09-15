"""Tool-output shaping models (DD-13, T14)."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TruncateStrategy(StrEnum):
    """How oversized tool output is truncated."""

    HEAD = "head"
    TAIL = "tail"
    SUMMARY = "summary"


class FilterAction(StrEnum):
    """Action applied to a tool-output pattern match."""

    REDACT = "redact"
    MASK = "mask"


class FilterRule(BaseModel):
    """A regex rule applied to tool output before it reaches the agent."""

    model_config = ConfigDict(extra="forbid")

    pattern: str
    action: FilterAction

    @field_validator("pattern")
    @classmethod
    def _pattern_must_compile(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"pattern is not a valid regex: {exc}") from exc
        return value


class OutputShapingSpec(BaseModel):
    """Tool-output boundary configuration for a workload."""

    model_config = ConfigDict(extra="forbid")

    max_bytes: int = Field(gt=0)
    truncate_strategy: TruncateStrategy = TruncateStrategy.HEAD
    filter_rules: list[FilterRule] = Field(default_factory=list)
    injection_scan: bool = True
