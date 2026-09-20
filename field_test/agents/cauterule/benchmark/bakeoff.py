"""Model bake-off harness — compares rule-extraction quality across LLM models."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory


@dataclass(frozen=True)
class ModelResult:
    """Result of running one model on a benchmark trajectory."""

    model_name: str
    candidate: CandidateRule | None
    extraction_time_ms: float
    extraction_error: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class BakeoffSummary:
    """Aggregate summary for a model across all benchmark trajectories."""

    model_name: str
    total: int
    succeeded: int
    failed: int
    avg_confidence: float
    avg_extraction_time_ms: float
    extraction_rate: float = 0.0

    def __post_init__(self) -> None:
        rate = self.succeeded / self.total if self.total > 0 else 0.0
        object.__setattr__(self, "extraction_rate", rate)


ExtractorFn = Callable[[Trajectory], CandidateRule | None]


class BakeoffHarness:
    """Harness that compares multiple models on the same benchmark corpus."""

    def __init__(self, models: Mapping[str, ExtractorFn]) -> None:
        self.models: dict[str, ExtractorFn] = dict(models)

    def run(self, trajectories: list[Trajectory]) -> dict[str, list[ModelResult]]:
        """Run all models on *trajectories* and return per-model results.

        A crashing model is recorded with ``extraction_error`` instead of
        aborting the entire bake-off (#611).
        """
        results: dict[str, list[ModelResult]] = {name: [] for name in self.models}
        for traj in trajectories:
            for name, extractor in self.models.items():
                t0 = time.perf_counter()
                candidate: CandidateRule | None = None
                error: str | None = None
                try:
                    candidate = extractor(traj)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                results[name].append(
                    ModelResult(
                        model_name=name,
                        candidate=candidate,
                        extraction_time_ms=elapsed_ms,
                        confidence=candidate.confidence if candidate else 0.0,
                        extraction_error=(
                            error or (None if candidate else "extraction returned None")
                        ),
                    )
                )
        return results

    def summarize(self, results: dict[str, list[ModelResult]]) -> dict[str, BakeoffSummary]:
        """Produce aggregate summaries from raw *results*."""
        summaries: dict[str, BakeoffSummary] = {}
        for name, model_results in results.items():
            total = len(model_results)
            succeeded = sum(1 for r in model_results if r.candidate is not None)
            failed = total - succeeded
            confs = [r.confidence for r in model_results if r.candidate is not None]
            times = [r.extraction_time_ms for r in model_results if r.candidate is not None]
            avg_conf = sum(confs) / len(confs) if confs else 0.0
            avg_time = sum(times) / len(times) if times else 0.0
            summaries[name] = BakeoffSummary(
                model_name=name,
                total=total,
                succeeded=succeeded,
                failed=failed,
                avg_confidence=avg_conf,
                avg_extraction_time_ms=avg_time,
            )
        return summaries
