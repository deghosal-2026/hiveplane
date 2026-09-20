"""Corpus replay diagnostics (#690).

A 0-pass result on a corpus can mean three very different things:

1. the matcher cannot match anything at the current threshold;
2. there is no reference corpus content for that failure class (nothing to
   match against); or
3. extraction never produces a non-degenerate candidate (a pre-filter /
   prompt issue, not a similarity issue).

The field-test report conflated these.  This module provides the pure
building blocks — a candidate pre-filter funnel and reference coverage — so
``scripts/diagnose_corpus.py`` can report which case applies per corpus.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory
from cauterule.replay.matcher import trigger_prefilter_reason

# A corpus type with fewer references than this is treated as under-covered,
# so a silent "empty reference set" regression is flagged rather than showing
# up only as an inexplicable 0-pass in a field sweep.
MIN_REFERENCE_TRAJECTORIES = 5


@dataclass(frozen=True)
class CandidateFunnel:
    """Where extracted candidates are lost before similarity scoring."""

    total: int
    empty: int
    degenerate: int
    too_short: int
    generic: int
    scored: int


def candidate_funnel(candidates: Iterable[CandidateRule]) -> CandidateFunnel:
    """Partition candidates by pre-filter rejection vs. reaching ``match_score``."""
    counts = {"empty": 0, "degenerate": 0, "too_short": 0, "generic": 0}
    total = 0
    scored = 0
    for candidate in candidates:
        total += 1
        reason = trigger_prefilter_reason(candidate)
        if reason is None:
            scored += 1
        else:
            counts[reason] += 1
    return CandidateFunnel(
        total=total,
        empty=counts["empty"],
        degenerate=counts["degenerate"],
        too_short=counts["too_short"],
        generic=counts["generic"],
        scored=scored,
    )


def _first_segment(failure_class: str | None) -> str:
    return (failure_class or "").split("/")[0].strip().lower()


def reference_coverage(references: Iterable[Trajectory]) -> dict[str, int]:
    """Count reference trajectories by their failure_class domain segment."""
    counter: Counter[str] = Counter()
    for traj in references:
        counter[_first_segment(traj.failure_class) or "unknown"] += 1
    return dict(sorted(counter.items()))


@dataclass(frozen=True)
class CorpusDiagnostic:
    """Structural diagnostic for one corpus type."""

    corpus_type: str
    target_count: int
    target_failure_classes: tuple[str, ...]
    reference_count: int
    reference_domains: dict[str, int]
    uncovered_domains: tuple[str, ...]
    reference_sufficient: bool


def diagnose_corpus(
    corpus_type: str,
    targets: Iterable[Trajectory],
    references: Iterable[Trajectory],
) -> CorpusDiagnostic:
    """Summarize target vs. reference coverage for *corpus_type*."""
    target_list = list(targets)
    reference_list = list(references)
    failure_classes = tuple(sorted({t.failure_class for t in target_list if t.failure_class}))
    domains = reference_coverage(reference_list)
    target_domains = {_first_segment(fc) for fc in failure_classes if fc}
    uncovered = tuple(sorted(d for d in target_domains if d and d not in domains))
    return CorpusDiagnostic(
        corpus_type=corpus_type,
        target_count=len(target_list),
        target_failure_classes=failure_classes,
        reference_count=len(reference_list),
        reference_domains=domains,
        uncovered_domains=uncovered,
        reference_sufficient=len(reference_list) >= MIN_REFERENCE_TRAJECTORIES,
    )


def load_jsonl_trajectories(path: str | Path) -> list[Trajectory]:
    """Load every non-blank line of a JSONL file as a :class:`Trajectory`."""
    trajectories: list[Trajectory] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            trajectories.append(Trajectory.from_dict(json.loads(stripped)))
    return trajectories
