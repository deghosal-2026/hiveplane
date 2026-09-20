"""Prompt bake-off harness — compares rule-extraction quality across prompt variants."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory


@dataclass(frozen=True)
class PromptResult:
    """Result of running one prompt variant on a benchmark trajectory."""

    prompt_name: str
    candidate: CandidateRule | None
    extraction_error: str | None = None
    confidence: float = 0.0


PromptFn = Callable[[Trajectory], CandidateRule | None]


class PromptBakeoffHarness:
    """Harness that compares prompt variants on the same benchmark corpus."""

    def __init__(self, prompt_fns: Mapping[str, PromptFn]) -> None:
        self.prompt_fns: dict[str, PromptFn] = dict(prompt_fns)

    def run(self, trajectories: list[Trajectory]) -> dict[str, list[PromptResult]]:
        """Run all prompt variants on *trajectories* and return per-prompt results.

        A crashing prompt function is recorded with ``extraction_error``
        instead of aborting the bake-off (#611).
        """
        results: dict[str, list[PromptResult]] = {name: [] for name in self.prompt_fns}
        for traj in trajectories:
            for name, prompt_fn in self.prompt_fns.items():
                candidate: CandidateRule | None = None
                error: str | None = None
                try:
                    candidate = prompt_fn(traj)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                results[name].append(
                    PromptResult(
                        prompt_name=name,
                        candidate=candidate,
                        confidence=candidate.confidence if candidate else 0.0,
                        extraction_error=(
                            error or (None if candidate else "extraction returned None")
                        ),
                    )
                )
        return results

    def best_prompt(self, results: dict[str, list[PromptResult]]) -> str:
        """Return the prompt name with the highest average confidence."""
        best_name: str = ""
        best_avg: float = -1.0
        for name, prompt_results in results.items():
            confs = [r.confidence for r in prompt_results if r.candidate is not None]
            if not confs:
                continue
            avg = sum(confs) / len(confs)
            if avg > best_avg:
                best_avg = avg
                best_name = name
        return best_name
