"""Corpus validation — size and annotation checks for safety corpora."""

from __future__ import annotations

from cauterule.models.trajectory import Trajectory

# Minimum sizes for statistical significance (issue #422).
MIN_SAFETY_SIZES: dict[str, int] = {
    "successes": 50,
    "failures/negative": 50,
    "nearmiss": 50,
}

# Raw corpora that require expected_outcome annotations (issue #423).
RAW_CORPORA: frozenset[str] = frozenset(
    {
        "raw/opencode",
        "raw/synthetic",
        "raw/ci",
        "raw/sibling-repos",
        "raw/corrections",
        "raw/cross-session",
    }
)


def validate_corpus_sizes(counts: dict[str, int]) -> list[str]:
    """Return warnings for safety corpora below minimum size."""
    warnings: list[str] = []
    for corpus, min_size in MIN_SAFETY_SIZES.items():
        actual = counts.get(corpus, 0)
        if actual < min_size:
            warnings.append(
                f"{corpus}: {actual} < {min_size} (need {min_size} for statistical significance)"
            )
    return warnings


def validate_annotations(trajectories: list[Trajectory]) -> list[str]:
    """Return warnings for raw trajectories missing expected_outcome."""
    warnings: list[str] = []
    for traj in trajectories:
        if traj.expected_outcome is None:
            warnings.append(f"{traj.id}: missing expected_outcome")
    return warnings


def corpus_size_report(counts: dict[str, int]) -> dict[str, int | bool]:
    """Return size report with pass/fail per safety corpus."""
    report: dict[str, int | bool] = {}
    for corpus, min_size in MIN_SAFETY_SIZES.items():
        actual = counts.get(corpus, 0)
        report[corpus] = actual
        report[f"{corpus}_ok"] = actual >= min_size
    report["all_ok"] = all(counts.get(c, 0) >= m for c, m in MIN_SAFETY_SIZES.items())
    return report
