"""Tests for the tool-output shaping pipeline."""

from __future__ import annotations

from hiveplane.core.shaping import (
    FilterAction,
    FilterRule,
    OutputShapingSpec,
    TruncateStrategy,
)
from hiveplane.shaping.injection import InjectionScanner, InjectionVerdict
from hiveplane.shaping.pipeline import OutputBudget, ShapingPipeline


def _spec(**overrides: object) -> OutputShapingSpec:
    data: dict[str, object] = {"max_bytes": 1000, "truncate_strategy": TruncateStrategy.HEAD}
    data.update(overrides)
    return OutputShapingSpec.model_validate(data)


def test_filter_redacts_and_masks() -> None:
    spec = _spec(
        filter_rules=[
            FilterRule(pattern=r"secret-\d+", action=FilterAction.REDACT),
            FilterRule(pattern=r"\b\d{4}-\d{4}\b", action=FilterAction.MASK),
        ]
    )
    shaped = ShapingPipeline().apply("token secret-42 card 1234-5678", spec)
    assert "[REDACTED]" in shaped.text
    assert "1234-5678" not in shaped.text
    assert shaped.redactions == 2


def test_truncate_head_bounds_bytes() -> None:
    spec = _spec(max_bytes=5, truncate_strategy=TruncateStrategy.HEAD)
    shaped = ShapingPipeline().apply("abcdefghij", spec)
    assert shaped.truncated is True
    assert shaped.text == "abcde"
    assert shaped.shaped_bytes == 5


def test_truncate_tail_bounds_bytes() -> None:
    spec = _spec(max_bytes=4, truncate_strategy=TruncateStrategy.TAIL)
    shaped = ShapingPipeline().apply("abcdefghij", spec)
    assert shaped.text == "ghij"


def test_truncate_summary_marks_truncation() -> None:
    spec = _spec(max_bytes=20, truncate_strategy=TruncateStrategy.SUMMARY)
    shaped = ShapingPipeline().apply("x" * 100, spec)
    assert shaped.truncated is True
    assert "truncated" in shaped.text
    assert shaped.shaped_bytes <= 20


def test_cumulative_budget_truncates_more_aggressively() -> None:
    spec = _spec(max_bytes=100)
    budget = OutputBudget(max_total_bytes=120)
    pipeline = ShapingPipeline()
    first = pipeline.apply("a" * 100, spec, budget=budget)
    assert first.truncated is False
    second = pipeline.apply("b" * 100, spec, budget=budget)
    assert second.shaped_bytes <= 20
    assert budget.remaining() == 0


def test_shaping_records_injection_verdict() -> None:
    spec = _spec(injection_scan=True)
    shaped = ShapingPipeline(InjectionScanner()).apply("safe output", spec)
    assert shaped.injection is not None
    assert shaped.injection.verdict is InjectionVerdict.NONE


def test_injection_scan_can_be_disabled() -> None:
    spec = _spec(injection_scan=False)
    shaped = ShapingPipeline(InjectionScanner()).apply("safe output", spec)
    assert shaped.injection is None


def test_original_and_shaped_bytes_are_reported() -> None:
    spec = _spec(max_bytes=1000)
    shaped = ShapingPipeline().apply("hello", spec)
    assert shaped.original_bytes == 5
    assert shaped.shaped_bytes == 5
    assert shaped.truncated is False
