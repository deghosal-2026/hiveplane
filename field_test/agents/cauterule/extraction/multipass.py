"""Multi-pass orchestrator."""

from __future__ import annotations

from typing import Any

from cauterule.extraction.dedup import deduplicate
from cauterule.extraction.extractor import extract_candidate_safe
from cauterule.extraction.gate import (
    SILENCE_REASON_NO_FAILURE,
    GateMode,
    run_gate,
)
from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory

DEFAULT_TEMPERATURES: tuple[float, ...] = (0.2, 0.5, 0.8)

__all__ = ["DEFAULT_TEMPERATURES", "SILENCE_REASON_NO_FAILURE", "multipass_extract"]


def multipass_extract(
    trajectory: Trajectory,
    llm: Any,
    temperatures: tuple[float, ...] = DEFAULT_TEMPERATURES,
    template: str | None = None,
    gate_mode: str = "strict",
    confidence_threshold: float = 0.6,
) -> list[CandidateRule]:
    """Run extraction multiple times with different temperatures.

    Before any LLM call, runs the pre-extraction gate. If the gate
    blocks extraction, returns an empty list.

    Args:
        trajectory: Trajectory to extract from.
        llm: LLM provider.
        temperatures: Temperatures to use per pass.
        template: Optional template hint.
        gate_mode: ``"strict"`` or ``"relaxed"`` (see :func:`~cauterule.extraction.gate.run_gate`).
        confidence_threshold: Quality-gate threshold; failing candidates
            never enter the tournament (#497).

    Returns:
        List of successfully extracted candidates (one per successful pass).
    """
    mode: GateMode = "relaxed" if gate_mode == "relaxed" else "strict"
    gate_result = run_gate(trajectory, mode=mode)
    if not gate_result.should_extract:
        return []

    candidates: list[CandidateRule] = []
    for idx, temp in enumerate(temperatures, start=1):
        candidate, error = extract_candidate_safe(
            trajectory,
            llm,
            template=template,
            extraction_pass=idx,
            temperature=temp,
            confidence_threshold=confidence_threshold,
        )
        if candidate is not None:
            candidates.append(candidate)
        else:
            _ = error
    # #732: collapse identical candidates produced by different passes so they
    # do not double-count in the tournament / candidate aggregates.
    return deduplicate(candidates)
