"""Tests for scheduled corpus expansion (M55-05)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hiveplane.certification.models import (
    BenchmarkCorpus,
    BenchmarkTask,
    BenchmarkTaskCheck,
    CheckType,
)
from hiveplane.corpus.expansion import CorpusExpansionService
from hiveplane.corpus.templates import TemplateKind, corpus_template

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def _base() -> BenchmarkCorpus:
    return corpus_template(TemplateKind.TRIAGE, corpus_id="triage")


def _approved_task() -> BenchmarkTask:
    return BenchmarkTask(
        id="learned-1",
        name="learned: triage",
        check=BenchmarkTaskCheck(type=CheckType.EXACT_MATCH, field="output", value="ok"),
        critical=True,
        profiles=["full"],
    )


def test_expansion_runs_reviewed_candidates_only() -> None:
    def integrator(workload: str, base: BenchmarkCorpus) -> BenchmarkCorpus:
        # Stand-in for CorpusVersionService.integrate, which only adds APPROVED.
        return base.model_copy(update={"tasks": [*base.tasks, _approved_task()]})

    service = CorpusExpansionService(integrator, clock=lambda: _NOW)
    base = _base()

    expanded = service.expand("triage", base)

    assert len(expanded.tasks) == len(base.tasks) + 1
    assert expanded.tasks[-1].id == "learned-1"
    # The base corpus is not mutated in place.
    assert len(base.tasks) != len(expanded.tasks)


def test_due_respects_the_interval() -> None:
    service = CorpusExpansionService(
        lambda workload, base: base, interval_seconds=86400, clock=lambda: _NOW
    )

    assert service.due("triage") is True
    service.expand("triage", _base())
    assert service.due("triage", at=_NOW + timedelta(hours=1)) is False
    assert service.due("triage", at=_NOW + timedelta(days=1)) is True


def test_run_due_is_a_no_op_before_the_interval() -> None:
    calls: list[str] = []

    def integrator(workload: str, base: BenchmarkCorpus) -> BenchmarkCorpus:
        calls.append(workload)
        return base

    service = CorpusExpansionService(
        integrator, interval_seconds=86400, clock=lambda: _NOW
    )
    service.expand("triage", _base())
    calls.clear()

    assert service.run_due("triage", _base(), at=_NOW + timedelta(hours=2)) is None
    assert calls == []
    assert service.run_due("triage", _base(), at=_NOW + timedelta(days=2)) is not None
    assert calls == ["triage"]
