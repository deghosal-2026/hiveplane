"""Corpus templates for common workload types (M55-02)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import JsonValue

from hiveplane.certification.models import (
    BenchmarkCorpus,
    BenchmarkTask,
    BenchmarkTaskCheck,
    CheckType,
    ExpectedOutcome,
)


class TemplateKind(StrEnum):
    """The known starter corpus templates."""

    REPO_AGENT = "repo-agent"
    TRIAGE = "triage"
    GENERATION = "generation"
    CLASSIFICATION = "classification"


def _task(
    task_id: str,
    name: str,
    *,
    input_data: dict[str, JsonValue],
    field: str,
    value: str,
    critical: bool,
    fast: bool,
    expected_outcome: str | None = None,
) -> BenchmarkTask:
    return BenchmarkTask(
        id=task_id,
        name=name,
        input=input_data,
        expected=(
            ExpectedOutcome(outcome=expected_outcome)
            if expected_outcome is not None
            else None
        ),
        check=BenchmarkTaskCheck(type=CheckType.EXACT_MATCH, field=field, value=value),
        critical=critical,
        profiles=["fast", "full"] if fast else ["full"],
    )


def _repo_agent(corpus_id: str, version: int) -> BenchmarkCorpus:
    return BenchmarkCorpus(
        id=corpus_id,
        version=version,
        tasks=[
            _task(
                "fix-small-bug",
                "Fix a small bug in a Python file",
                input_data={"repo": "demo", "issue": "off-by-one in total()"},
                field="output",
                value="patched",
                critical=True,
                fast=True,
                expected_outcome="patched",
            ),
            _task(
                "add-test",
                "Add a regression test for the fix",
                input_data={"repo": "demo", "issue": "test total()"},
                field="output",
                value="test-added",
                critical=True,
                fast=False,
                expected_outcome="test-added",
            ),
            _task(
                "refactor",
                "Refactor a helper without changing behavior",
                input_data={"repo": "demo", "target": "total"},
                field="output",
                value="refactored",
                critical=False,
                fast=False,
            ),
        ],
    )


def _triage(corpus_id: str, version: int) -> BenchmarkCorpus:
    return BenchmarkCorpus(
        id=corpus_id,
        version=version,
        tasks=[
            _task(
                "classify-severity",
                "Assign a severity to an incident report",
                input_data={"report": "Checkout returns 500 for all users"},
                field="severity",
                value="critical",
                critical=True,
                fast=True,
            ),
            _task(
                "route-owner",
                "Route an incident to the owning team",
                input_data={"report": "Checkout returns 500 for all users"},
                field="team",
                value="payments",
                critical=True,
                fast=False,
            ),
        ],
    )


def _generation(corpus_id: str, version: int) -> BenchmarkCorpus:
    return BenchmarkCorpus(
        id=corpus_id,
        version=version,
        tasks=[
            _task(
                "summarize",
                "Summarize a short article",
                input_data={"article": "..."},
                field="output",
                value="summary",
                critical=True,
                fast=True,
            ),
            _task(
                "translate",
                "Translate a sentence",
                input_data={"text": "hello", "target": "fr"},
                field="output",
                value="bonjour",
                critical=False,
                fast=False,
            ),
        ],
    )


def _classification(corpus_id: str, version: int) -> BenchmarkCorpus:
    return BenchmarkCorpus(
        id=corpus_id,
        version=version,
        tasks=[
            _task(
                "sentiment",
                "Classify the sentiment of a review",
                input_data={"text": "I love it"},
                field="label",
                value="positive",
                critical=True,
                fast=True,
            ),
            _task(
                "topic",
                "Classify the topic of a document",
                input_data={"text": "Quarterly revenue grew"},
                field="label",
                value="finance",
                critical=False,
                fast=False,
            ),
        ],
    )


_TEMPLATES = {
    TemplateKind.REPO_AGENT: _repo_agent,
    TemplateKind.TRIAGE: _triage,
    TemplateKind.GENERATION: _generation,
    TemplateKind.CLASSIFICATION: _classification,
}


def corpus_template(
    kind: TemplateKind, *, corpus_id: str, version: int = 1
) -> BenchmarkCorpus:
    """Return a starter corpus for a workload type."""
    try:
        builder = _TEMPLATES[kind]
    except KeyError as exc:  # pragma: no cover - guarded by the enum
        raise ValueError(f"unknown corpus template: {kind}") from exc
    return builder(corpus_id, version)
