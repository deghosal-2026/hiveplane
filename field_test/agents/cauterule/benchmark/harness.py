"""Harness health assertions — the benchmark asserts on itself."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_PARSE_RATE_THRESHOLD = 0.7
DEFAULT_COMPLETION_THRESHOLD = 0.9


@dataclass(frozen=True)
class HealthCheck:
    """Single harness health check."""

    name: str
    passed: bool
    message: str = ""
    value: float | None = None
    threshold: float | None = None


@dataclass(frozen=True)
class HarnessHealth:
    """Aggregate harness health."""

    passed: bool
    checks: tuple[HealthCheck, ...] = field(default_factory=tuple)

    @property
    def warnings(self) -> list[str]:
        return [c.message for c in self.checks if not c.passed]


def check_parse_rate(
    parsed: int, total: int, threshold: float = DEFAULT_PARSE_RATE_THRESHOLD
) -> HealthCheck:
    """Check parse rate floor."""
    rate = (parsed / total) if total else 0.0
    passed = rate >= threshold
    msg = f"Parse rate {rate:.1%} ({parsed}/{total})"
    if not passed:
        msg += f" below threshold {threshold:.0%} — harness defect suspected"
    return HealthCheck(
        name="parse_rate", passed=passed, message=msg, value=rate, threshold=threshold
    )


def check_completion_ratio(
    candidates: int,
    trajectories: int,
    threshold: float = DEFAULT_COMPLETION_THRESHOLD,
) -> HealthCheck:
    """Check corpus completion ratio (candidates per trajectory)."""
    ratio = (candidates / trajectories) if trajectories else 0.0
    passed = ratio >= threshold or (candidates == 0 and trajectories == 0)
    # For safety corpora, 0 candidates may be correct (silence); this check is for positive corpora.
    # Caller should decide threshold; default is lenient.
    msg = f"Completion {candidates}/{trajectories} ({ratio:.1%})"
    if not passed:
        msg += " — harness failure suspected (0 candidates from trajectories)"
    return HealthCheck(
        name="completion_ratio", passed=passed, message=msg, value=ratio, threshold=threshold
    )


def check_candidate_range(
    candidates: int,
    corpus_name: str,
    expected_min: int,
    expected_max: int,
) -> HealthCheck:
    """Check per-corpus expected candidate range."""
    passed = expected_min <= candidates <= expected_max
    msg = f"{corpus_name}: {candidates} candidates (expected {expected_min}-{expected_max})"
    if not passed:
        msg += " — HARNESS_FAILURE"
    return HealthCheck(
        name=f"candidate_range:{corpus_name}", passed=passed, message=msg, value=float(candidates)
    )


def harness_health(
    *,
    parsed: int,
    total: int,
    candidates: int,
    trajectories: int,
    attempted: int | None = None,
    corpus_ranges: dict[str, tuple[int, int]] | None = None,
    candidate_counts: dict[str, int] | None = None,
    parse_threshold: float = DEFAULT_PARSE_RATE_THRESHOLD,
    is_safety_corpus: bool = False,
) -> HarnessHealth:
    """Run all harness health checks.

    Args:
        parsed: Number of successfully parsed candidates.
        total: Total trajectories in the corpus.
        candidates: Total candidates produced.
        trajectories: Total trajectories.
        attempted: Trajectories that actually reached the LLM
            (``total - gate_dropped``). The parse-rate is measured over these,
            not ``total`` — a safety/rejection corpus intentionally
            gate-drops many trajectories, and counting them as "unparsed"
            would mis-report a correct harness as defective. Defaults to
            ``total`` (correct for extraction corpora, which don't gate-drop).
        corpus_ranges: Optional {corpus_name: (min, max)} for candidate range checks.
        candidate_counts: Optional {corpus_name: count} for range checks.
        parse_threshold: Parse rate floor (default 0.7).
        is_safety_corpus: True for silence *and* rejection corpora
            (successes, failures/negative, nearmiss, adversarial/*) where
            producing candidates is not required. 0 attempted (everything
            gate-dropped) is a pass; otherwise only the parse rate is asserted.

    Returns:
        :class:`HarnessHealth` with PASS/FAIL.
    """
    checks: list[HealthCheck] = []
    if attempted is None:
        attempted = total

    if is_safety_corpus and attempted == 0:
        # Every trajectory was gate-dropped — silence is the pass condition.
        checks.append(
            HealthCheck(
                name="parse_rate",
                passed=True,
                message=f"Safety corpus: 0 candidates expected (gate dropped all {total} trajectories)",
                value=0.0,
                threshold=parse_threshold,
            )
        )
        checks.append(
            HealthCheck(
                name="completion_ratio",
                passed=True,
                message=f"Safety corpus: 0/{trajectories} completion expected (gate silence)",
                value=0.0,
                threshold=1.0,
            )
        )
    elif is_safety_corpus:
        # Rejection corpus (nearmiss/adversarial): candidates may be produced
        # and are expected to be blocked in replay. The only harness question is
        # whether the LLM output that *was* requested parsed cleanly, measured
        # over the attempted (non-gate-dropped) trajectories.
        checks.append(check_parse_rate(parsed, attempted, threshold=parse_threshold))
        checks.append(
            HealthCheck(
                name="completion_ratio",
                passed=True,
                message=f"Safety corpus: {candidates} candidates produced (replay rejection expected, not a harness defect)",
                value=float(candidates),
                threshold=None,
            )
        )
    else:
        checks.append(check_parse_rate(parsed, attempted, threshold=parse_threshold))
        if trajectories > 0 and candidates == 0:
            checks.append(
                HealthCheck(
                    name="completion_ratio",
                    passed=False,
                    message=f"Completion 0/{trajectories} — harness failure suspected",
                    value=0.0,
                    threshold=1.0,
                )
            )
        else:
            checks.append(check_completion_ratio(candidates, attempted))

    if corpus_ranges and candidate_counts:
        for corpus, (exp_min, exp_max) in corpus_ranges.items():
            count = candidate_counts.get(corpus, 0)
            checks.append(check_candidate_range(count, corpus, exp_min, exp_max))

    passed = all(c.passed for c in checks)
    return HarnessHealth(passed=passed, checks=tuple(checks))
