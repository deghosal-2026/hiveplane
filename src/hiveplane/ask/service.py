"""The `ask` operator copilot: read-only NL Q&A over live state (M53, D36)."""

from __future__ import annotations

import re

from pydantic import JsonValue

from hiveplane.ask.models import AskAnswer, AskIntent
from hiveplane.ask.readers import AskReaders
from hiveplane.persistence.audit import AuditLog


def _as_float(value: JsonValue) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _as_list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []

_MUTATION_WORDS = (
    "stop",
    "restart",
    "kill",
    "promote",
    "disable",
    "delete",
    "pause",
    "resume",
    "rollback",
    "halt",
    "shutdown",
)
_RUN_ID = re.compile(r"run\s+([A-Za-z0-9_.\-]+)", re.IGNORECASE)
_TEAM = re.compile(r"team\s+([A-Za-z0-9_.\-]+)", re.IGNORECASE)
_WORKLOAD = re.compile(
    r"health of\s+([A-Za-z0-9_.\-]+)|([A-Za-z0-9_.\-]+)\s+health", re.IGNORECASE
)
_APPROVAL = re.compile(r"approval\s+([A-Za-z0-9_.\-]+)", re.IGNORECASE)


def _period(question: str) -> str:
    lowered = question.lower()
    if "today" in lowered or "last day" in lowered:
        return "day"
    if "month" in lowered:
        return "month"
    return "week"


class AskService:
    """Answers attributed, read-only questions from injected live-state readers."""

    def __init__(self, readers: AskReaders, *, audit: AuditLog | None = None) -> None:
        self._readers = readers
        self._audit = audit

    def query(
        self,
        question: str,
        *,
        tenant_id: str = "default",
        operator_id: str = "anonymous",
    ) -> AskAnswer:
        """Resolve and answer a question; never mutate state."""
        if self._audit is not None:
            self._audit.append(
                operator_id,
                "ask.query",
                question,
                detail=f"tenant={tenant_id}",
            )
        intent = self._classify(question)
        answer = self._answer(question, intent, tenant_id=tenant_id)
        return AskAnswer(
            question=question,
            intent=intent,
            answer=answer.text,
            evidence=answer.evidence,
            citations=answer.citations,
            requires_confirmation=answer.requires_confirmation,
            tenant_id=tenant_id,
            attributed_to=operator_id,
        )

    @staticmethod
    def _classify(question: str) -> AskIntent:
        lowered = question.lower()
        if "who approved" in lowered or "who decided" in lowered:
            return AskIntent.APPROVAL_ATTRIBUTION
        if "fail" in lowered and _RUN_ID.search(question):
            return AskIntent.RUN_FAILURE
        if "spend" in lowered or "cost" in lowered:
            return AskIntent.TEAM_SPEND
        if "health" in lowered:
            return AskIntent.WORKLOAD_HEALTH
        if any(re.search(rf"\b{word}\b", lowered) for word in _MUTATION_WORDS):
            return AskIntent.MUTATION
        if "running" in lowered or "queue" in lowered or "fleet" in lowered:
            return AskIntent.FLEET_STATUS
        return AskIntent.UNKNOWN

    def _answer(
        self, question: str, intent: AskIntent, *, tenant_id: str
    ) -> _Answer:
        if intent is AskIntent.RUN_FAILURE:
            match = _RUN_ID.search(question)
            run_id = match.group(1) if match else ""
            data = self._readers.run(run_id, tenant_id=tenant_id)
            reason = data.get("failure_reason") or "no failure reason recorded"
            return _Answer(
                f"Run {run_id} {data.get('state')}: {reason}",
                evidence=dict(data),
                citations=[f"run:{run_id}"],
            )
        if intent is AskIntent.TEAM_SPEND:
            match = _TEAM.search(question)
            team = match.group(1) if match else ""
            period = _period(question)
            data = self._readers.team_spend(team, period, tenant_id=tenant_id)
            total = _as_float(data.get("total_cost_usd"))
            return _Answer(
                f"Team {team} spent ${total:.2f} ({period})",
                evidence=dict(data),
                citations=[f"team:{team}"],
            )
        if intent is AskIntent.APPROVAL_ATTRIBUTION:
            match = _APPROVAL.search(question)
            if match is None:
                return _Answer(
                    "Specify an approval id to see who decided it."
                )
            approval_id = match.group(1)
            data = self._readers.approval(approval_id, tenant_id=tenant_id)
            operator = data.get("operator") or "unknown"
            return _Answer(
                f"Approval {approval_id} was {data.get('status')} by {operator}",
                evidence=dict(data),
                citations=[f"approval:{approval_id}"],
            )
        if intent is AskIntent.WORKLOAD_HEALTH:
            match = _WORKLOAD.search(question)
            workload = (match.group(1) or match.group(2)) if match else ""
            data = self._readers.health(workload, tenant_id=tenant_id)
            return _Answer(
                f"{workload} is {data.get('status')}",
                evidence=dict(data),
                citations=[f"workload:{workload}"],
            )
        if intent is AskIntent.FLEET_STATUS:
            data = self._readers.fleet(tenant_id=tenant_id)
            running = _as_list(data.get("running"))
            paused = _as_list(data.get("paused"))
            return _Answer(
                f"{len(running)} runs running, {len(paused)} paused",
                evidence=dict(data),
                citations=["fleet"],
            )
        if intent is AskIntent.MUTATION:
            return _Answer(
                "This is a mutating request; `ask` is read-only. Run the "
                "explicit operator command and confirm it.",
                requires_confirmation=True,
            )
        return _Answer(
            "I can answer questions about run failures, team spend, approval "
            "attribution, workload health, and what is running."
        )


class _Answer:
    """Internal answer payload before attribution."""

    __slots__ = ("citations", "evidence", "requires_confirmation", "text")

    def __init__(
        self,
        text: str,
        *,
        evidence: dict[str, JsonValue] | None = None,
        citations: list[str] | None = None,
        requires_confirmation: bool = False,
    ) -> None:
        self.text = text
        self.evidence = evidence or {}
        self.citations = citations or []
        self.requires_confirmation = requires_confirmation
