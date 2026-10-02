"""LLM judge: score a production run against a versioned rubric (M36-05).

Mirrors the router classifier's shape: a provider-neutral ``CompletionRequest``
with a strict JSON contract, parsed and clamped to ``[0, 1]``. The rubric version
pins the evaluator identity, so a rubric edit never silently changes score
semantics.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.budget.errors import UnknownModelPriceError
from hiveplane.budget.pricing import CostTable
from hiveplane.core.run import Run
from hiveplane.learning.models import (
    JudgeCriterionScore,
    JudgeResult,
    Rubric,
)
from hiveplane.llm.models import CompletionRequest, CompletionResponse, Message
from hiveplane.llm.provider import LLMProvider

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _clamp(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _prompt(run: Run, rubric: Rubric) -> str:
    criteria = "\n".join(
        f"- {criterion.name}: {criterion.description}" for criterion in rubric.criteria
    )
    return (
        "You are grading an agent run against a rubric.\n"
        f"Rubric: {rubric.name} v{rubric.version}\n"
        f"Criteria:\n{criteria}\n\n"
        f"Task input: {json.dumps(run.task, sort_keys=True, default=str)}\n"
        f"Agent output: {json.dumps(run.result, sort_keys=True, default=str)}\n\n"
        "Respond with strict JSON: "
        '{"score": <0..1>, "criteria": [{"criterion": "<name>", "score": <0..1>}]}'
    )


class RubricJudge:
    """Scores runs with an LLM under a versioned rubric."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str,
        cost_table: CostTable | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._cost_table = cost_table
        self._clock = clock or (lambda: datetime.now(UTC))

    def _cost_usd(self, response: CompletionResponse) -> float:
        """Price a judge call, treating unpriced judge models as free."""
        if self._cost_table is None:
            return 0.0
        try:
            return float(
                self._cost_table.price(
                    response.model_identity,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
            )
        except UnknownModelPriceError:
            return 0.0

    def judge(self, run: Run, rubric: Rubric) -> JudgeResult:
        """Return a bounded score and per-criterion breakdown for a run."""
        response = self._provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content=_prompt(run, rubric))],
                model=self._model,
                metadata={"workload": run.workload_id, "run_id": run.id},
            )
        )
        cost = self._cost_usd(response)
        match = _JSON_OBJECT.search(response.content)
        if match is None:
            return JudgeResult(
                score=0.0,
                criteria=[],
                model_identity=response.model_identity,
                cost_usd=cost,
            )
        try:
            document = json.loads(match.group(0))
        except json.JSONDecodeError:
            return JudgeResult(
                score=0.0,
                criteria=[],
                model_identity=response.model_identity,
                cost_usd=cost,
            )
        criteria = [
            JudgeCriterionScore(
                criterion=str(item.get("criterion", "")),
                score=_clamp(item.get("score")),
            )
            for item in document.get("criteria", [])
            if isinstance(item, dict) and item.get("criterion")
        ]
        return JudgeResult(
            score=_clamp(document.get("score")),
            criteria=criteria,
            model_identity=response.model_identity,
            cost_usd=cost,
        )
