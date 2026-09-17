"""Tests for the adapter reporting seam."""

from __future__ import annotations

from hiveplane.adapters.reporter import RunReporter
from hiveplane.core.event import EventType
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport


class _Reporter:
    def get(self, run_id: str) -> Run:
        raise NotImplementedError

    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        detail: str | None = None,
        failure_reason: str | None = None,
        result: object | None = None,
    ) -> Run:
        raise NotImplementedError

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        raise NotImplementedError

    def record_event(
        self, run_id: str, event_type: EventType, actor: str, *, detail: str | None = None
    ) -> None:
        raise NotImplementedError


def test_reporter_protocol_is_runtime_checkable() -> None:
    assert isinstance(_Reporter(), RunReporter)


def test_incomplete_object_is_not_a_reporter() -> None:
    assert not isinstance(object(), RunReporter)
