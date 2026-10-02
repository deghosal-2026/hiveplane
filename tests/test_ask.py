"""Tests for the `ask` natural-language operator copilot (M53)."""

from __future__ import annotations

from typing import Any

import pytest

from hiveplane.ask.models import AskAnswer, AskIntent
from hiveplane.ask.service import AskService
from hiveplane.ask.workload import ASK_WORKLOAD_NAME, ask_corpus, ask_manifest
from hiveplane.persistence.audit import InMemoryAuditLog


class FakeReaders:
    """A scriptable, read-only live-state reader for tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.run_data: dict[str, Any] = {
            "id": "42",
            "state": "failed",
            "failure_reason": "tool timeout",
        }
        self.spend_data: dict[str, Any] = {
            "team_id": "platform",
            "total_cost_usd": 12.5,
            "period": "week",
        }
        self.approval_data: dict[str, Any] = {
            "approval_id": "ap-1",
            "status": "approved",
            "operator": "alice",
            "reason": "destructive call",
        }
        self.health_data: dict[str, Any] = {
            "workload": "summarizer",
            "status": "degraded",
            "failure_rate": 0.2,
        }
        self.fleet_data: dict[str, Any] = {
            "running": ["run-1", "run-2"],
            "paused": [],
        }

    def run(self, run_id: str, *, tenant_id: str) -> dict[str, Any]:
        self.calls.append(("run", (run_id, tenant_id)))
        return self.run_data

    def team_spend(self, team: str, period: str, *, tenant_id: str) -> dict[str, Any]:
        self.calls.append(("team_spend", (team, period, tenant_id)))
        return self.spend_data

    def approval(self, approval_id: str, *, tenant_id: str) -> dict[str, Any]:
        self.calls.append(("approval", (approval_id, tenant_id)))
        return self.approval_data

    def health(self, workload: str, *, tenant_id: str) -> dict[str, Any]:
        self.calls.append(("health", (workload, tenant_id)))
        return self.health_data

    def fleet(self, *, tenant_id: str) -> dict[str, Any]:
        self.calls.append(("fleet", (tenant_id,)))
        return self.fleet_data


def _service(
    readers: FakeReaders | None = None, audit: InMemoryAuditLog | None = None
) -> tuple[AskService, FakeReaders]:
    resolved = readers or FakeReaders()
    return AskService(resolved, audit=audit), resolved


def test_run_failure_question_is_answered_from_live_state() -> None:
    service, readers = _service()

    answer = service.query("why did run 42 fail?", operator_id="alice")

    assert answer.intent is AskIntent.RUN_FAILURE
    assert "tool timeout" in answer.answer
    assert answer.read_only is True
    assert ("run", ("42", "default")) in readers.calls


def test_team_spend_question_is_answered() -> None:
    service, readers = _service()

    answer = service.query("show team platform spend last week")

    assert answer.intent is AskIntent.TEAM_SPEND
    assert "$12.50" in answer.answer
    assert ("team_spend", ("platform", "week", "default")) in readers.calls


def test_approval_attribution_question_is_answered() -> None:
    service, readers = _service()

    answer = service.query("who approved the destructive call?")

    assert answer.intent is AskIntent.APPROVAL_ATTRIBUTION

    answer2 = service.query("who approved approval ap-1?")
    assert "alice" in answer2.answer
    assert ("approval", ("ap-1", "default")) in readers.calls


def test_workload_health_question_is_answered() -> None:
    service, _ = _service()

    answer = service.query("what is the health of summarizer?")

    assert answer.intent is AskIntent.WORKLOAD_HEALTH
    assert "degraded" in answer.answer


def test_fleet_status_question_is_answered() -> None:
    service, _ = _service()

    answer = service.query("what is running right now?")

    assert answer.intent is AskIntent.FLEET_STATUS
    assert "2" in answer.answer


def test_unknown_question_is_reported_honestly() -> None:
    service, _ = _service()

    answer = service.query("what is the meaning of life?")

    assert answer.intent is AskIntent.UNKNOWN
    assert answer.answer


def test_mutation_question_requires_confirmation_and_never_mutates() -> None:
    service, readers = _service()

    answer = service.query("restart run 42")

    assert answer.requires_confirmation is True
    assert answer.read_only is True
    assert readers.calls == []


def test_queries_are_attributed_to_the_caller() -> None:
    audit = InMemoryAuditLog()
    service, _ = _service(audit=audit)

    service.query("what is running?", operator_id="alice", tenant_id="acme")

    record = audit.records()[0]
    assert record.actor == "alice"
    assert record.action == "ask.query"


def test_answer_carries_tenant_and_operator() -> None:
    service, _ = _service()

    answer = service.query("what is running?", operator_id="alice", tenant_id="acme")

    assert isinstance(answer, AskAnswer)
    assert answer.tenant_id == "acme"
    assert answer.attributed_to == "alice"


def test_fixed_question_corpus_is_answered() -> None:
    service, _ = _service()
    corpus = ask_corpus()

    assert len(corpus.tasks) >= 5
    for task in corpus.tasks:
        answer = service.query(str(task.input["question"]), operator_id="alice")
        assert answer.intent is not AskIntent.UNKNOWN


def test_ask_manifest_is_a_valid_workload() -> None:
    manifest = ask_manifest()

    assert manifest.name == ASK_WORKLOAD_NAME
    assert manifest.spec.budget.per_run_usd > 0


def test_ask_is_read_only_by_construction() -> None:
    service, readers = _service()
    assert not any(
        hasattr(readers, name)
        for name in ("pause", "stop", "promote", "disable", "delete")
    )
    answer = service.query("stop the fleet")
    assert answer.requires_confirmation is True


@pytest.mark.parametrize(
    "question",
    [
        "why did run 7 fail",
        "show team ops spend this month",
        "who approved the destructive call",
        "health of summarizer",
        "what is running right now",
    ],
)
def test_common_phrasings_resolve(question: str) -> None:
    service, _ = _service()

    assert service.query(question).intent is not AskIntent.UNKNOWN


def test_query_forwards_the_caller_tenant_to_readers() -> None:
    service, readers = _service()

    service.query("what is running right now?", tenant_id="acme")

    assert ("fleet", ("acme",)) in readers.calls


def test_ask_workload_entrypoint_answers_corpus_question() -> None:
    from hiveplane.ask import entrypoint

    service, _ = _service()
    entrypoint.bind(service, operator_id="ask", tenant_id="acme")
    try:
        output = entrypoint.run({"question": "what is running right now?"})
        assert output["intent"] == "fleet_status"
        assert output["tenant_id"] == "acme"
    finally:
        entrypoint._service = None

