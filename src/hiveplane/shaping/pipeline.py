"""Tool-output shaping: filter, truncate, budget, and scan (D13, DD-13)."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.shaping import (
    FilterAction,
    FilterRule,
    OutputShapingSpec,
    TruncateStrategy,
)
from hiveplane.shaping.injection import InjectionScanner, InjectionScanResult

_TRUNCATION_MARKER = "...[truncated]"


class OutputBudget:
    """Cumulative per-run tool-output byte budget."""

    def __init__(self, max_total_bytes: int | None = None) -> None:
        self._max = max_total_bytes
        self._used = 0

    def remaining(self) -> int:
        """Return the remaining byte allowance (effectively unbounded when no max)."""
        if self._max is None:
            return 1 << 62
        return max(0, self._max - self._used)

    def consume(self, amount: int) -> None:
        """Record consumed bytes."""
        self._used += amount


class ShapedOutput(BaseModel):
    """The result of shaping a tool output before it reaches the agent."""

    model_config = ConfigDict(extra="forbid")

    text: str
    original_bytes: int = Field(ge=0)
    shaped_bytes: int = Field(ge=0)
    truncated: bool
    redactions: int = Field(default=0, ge=0)
    injection: InjectionScanResult | None = None


def _apply_filter_rule(text: str, rule: FilterRule) -> tuple[str, int]:
    pattern = re.compile(rule.pattern)
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        if rule.action is FilterAction.MASK:
            return "*" * len(match.group(0))
        return "[REDACTED]"

    return pattern.sub(_replace, text), count


def _filter(text: str, spec: OutputShapingSpec) -> tuple[str, int]:
    redactions = 0
    for rule in spec.filter_rules:
        text, added = _apply_filter_rule(text, rule)
        redactions += added
    return text, redactions


def _truncate(text: str, limit: int, strategy: TruncateStrategy) -> str:
    if limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    if strategy is TruncateStrategy.HEAD:
        return encoded[:limit].decode("utf-8", errors="ignore")
    if strategy is TruncateStrategy.TAIL:
        return encoded[-limit:].decode("utf-8", errors="ignore")
    marker = _TRUNCATION_MARKER.encode("utf-8")
    head = max(0, limit - len(marker))
    return (encoded[:head] + marker)[:limit].decode("utf-8", errors="ignore")


class ShapingPipeline:
    """Applies filtering, truncation, budget, and injection scanning."""

    def __init__(self, scanner: InjectionScanner | None = None) -> None:
        self._scanner = scanner

    def apply(
        self,
        text: str,
        spec: OutputShapingSpec,
        *,
        budget: OutputBudget | None = None,
    ) -> ShapedOutput:
        """Shape a tool output according to the workload's shaping spec."""
        original_bytes = len(text.encode("utf-8"))
        filtered, redactions = _filter(text, spec)
        limit = spec.max_bytes
        if budget is not None:
            limit = min(limit, budget.remaining())
        shaped_bytes = len(filtered.encode("utf-8"))
        truncated = shaped_bytes > limit
        if truncated:
            filtered = _truncate(filtered, limit, spec.truncate_strategy)
            shaped_bytes = len(filtered.encode("utf-8"))
        if budget is not None:
            budget.consume(shaped_bytes)
        injection = None
        if spec.injection_scan and self._scanner is not None:
            injection = self._scanner.scan(filtered)
        return ShapedOutput(
            text=filtered,
            original_bytes=original_bytes,
            shaped_bytes=shaped_bytes,
            truncated=truncated,
            redactions=redactions,
            injection=injection,
        )
