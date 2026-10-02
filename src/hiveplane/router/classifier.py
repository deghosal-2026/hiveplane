"""The cheap-model task classifier behind the router (M30-01).

The classifier scores only the certified candidates it is given, using the
provider seam (D17). It returns a provider-neutral, normalized score per
candidate; the router applies the confidence and margin guardrails.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.llm.models import CompletionRequest, Message
from hiveplane.llm.provider import LLMProvider
from hiveplane.router.catalog import CatalogCandidate

_SYSTEM_PROMPT = (
    "You are a conservative task router for a fleet of certified agent workloads. "
    "Given a user task and a list of candidate workloads, score how well each "
    "candidate fits the task from 0.0 (no fit) to 1.0 (perfect fit). "
    "Only score the listed candidates. Respond with a single JSON object of the "
    'form {"scores": {"<workload>": <number>, ...}} and nothing else.'
)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class ClassifierError(Exception):
    """Raised when a classifier response cannot be parsed."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"classifier response could not be parsed: {reason}")
        self.reason = reason


class ClassificationResult(BaseModel):
    """Normalized scores for the candidate workloads plus model identity."""

    model_config = ConfigDict(extra="forbid")

    model_identity: str = Field(min_length=1)
    scores: dict[str, float] = Field(default_factory=dict)


class TaskClassifier(Protocol):
    """Scores candidate workloads for a plain-language task."""

    def classify(
        self, task: str, candidates: list[CatalogCandidate]
    ) -> ClassificationResult: ...


def _build_prompt(task: str, candidates: list[CatalogCandidate]) -> str:
    lines = [f"- {candidate.workload}: {candidate.description or '(no description)'}"
             for candidate in candidates]
    return "Candidates:\n" + "\n".join(lines) + f"\n\nTask: {task}\n\nScores:"


def _parse_scores(content: str) -> dict[str, float]:
    match = _JSON_OBJECT.search(content)
    if match is None:
        raise ClassifierError("no JSON object in response")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ClassifierError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise ClassifierError("response is not a JSON object")
    raw = payload.get("scores", payload)
    if not isinstance(raw, dict):
        raise ClassifierError("'scores' is not an object")
    scores: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        scores[str(key)] = min(1.0, max(0.0, float(value)))
    if not scores:
        raise ClassifierError("no numeric scores")
    return scores


class LLMTaskClassifier:
    """A classifier that asks a (cheap) model through the provider seam."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str,
        max_tokens: int = 512,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens

    def classify(
        self, task: str, candidates: list[CatalogCandidate]
    ) -> ClassificationResult:
        """Return normalized scores for ``candidates`` (unknown names ignored)."""
        if not candidates:
            raise ClassifierError("no candidates to classify")
        request = CompletionRequest(
            messages=[
                Message(role="system", content=_SYSTEM_PROMPT),
                Message(role="user", content=_build_prompt(task, candidates)),
            ],
            model=self._model,
            temperature=0.0,
            max_tokens=self._max_tokens,
        )
        response = self._provider.complete(request)
        scores = _parse_scores(response.content)
        eligible = {candidate.workload for candidate in candidates}
        normalized = {
            workload: score
            for workload, score in scores.items()
            if workload in eligible
        }
        if not normalized:
            raise ClassifierError("no scores matched the candidate set")
        return ClassificationResult(
            model_identity=response.model_identity, scores=normalized
        )
