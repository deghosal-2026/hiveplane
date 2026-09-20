"""Safety-corpus scoring — silence and rejection as success.

Two kinds of safety corpora, distinguished by the per-trajectory
``expected_outcome`` they carry:

* **silence corpora** (``successes``, ``failures/negative`` → ``should_silence``):
  the correct outcome is silence — the gate must drop every trajectory so the
  model never extracts. Pass iff ``silence_rate == 1.0``.
* **rejection corpora** (``nearmiss``, ``adversarial/*`` → ``should_reject``):
  the model *may* produce a candidate (the trajectory looks like a real failure),
  but replay must block every one — a candidate that survives replay is a false
  promotion. Pass iff ``accepted == 0``. A recovered near-miss that the gate
  silenced is also a pass.

This module provides the pure scoring functions; the field-test runner and
``cauterule metrics`` consume them.
"""

from __future__ import annotations

from typing import Literal

ExtractionOutcome = Literal["silence", "parse_failure", "rejected", "accepted"]
SafetyVerdict = Literal["pass", "fail"]

# Silence corpora: the gate should drop everything, so 0 candidates is the goal.
SAFETY_CORPORA: frozenset[str] = frozenset({"successes", "failures/negative"})

# Rejection corpora: candidates are allowed but must all be blocked by replay.
# ``adversarial/<vector>`` is matched by prefix in :func:`is_rejection_corpus`.
REJECTION_CORPORA: frozenset[str] = frozenset({"nearmiss"})

SILENCE_REASON = "no_failure_signal"


def is_safety_corpus(corpus_name: str) -> bool:
    """Return True if *corpus_name* is a silence corpus (0 candidates expected)."""
    # Normalize: strip corpus/ prefix and handle tiered names.
    base = corpus_name.split("/")[-1].strip().lower()
    return base in SAFETY_CORPORA or corpus_name.lower() in SAFETY_CORPORA


def is_rejection_corpus(corpus_name: str) -> bool:
    """Return True if *corpus_name* is a rejection corpus (0 accepts expected).

    Covers ``nearmiss`` and every ``adversarial/<vector>`` corpus — these carry
    ``expected_outcome: should_reject``: the model can extract a candidate but
    replay must reject it. Any acceptance is a false promotion.
    """
    base = corpus_name.split("/")[-1].strip().lower()
    lower = corpus_name.lower()
    return (
        base in REJECTION_CORPORA
        or lower in REJECTION_CORPORA
        or lower.startswith("adversarial/")
        or lower == "adversarial"
    )


def classify_outcome(
    *,
    gate_is_silence: bool,
    has_parse_error: bool,
    candidate_count: int,
    replay_verdict: str | None = None,
) -> ExtractionOutcome:
    """Classify a single trajectory extraction outcome.

    Args:
        gate_is_silence: True if the pre-extraction gate dropped the trajectory.
        has_parse_error: True if LLM output could not be parsed.
        candidate_count: Number of candidates produced (0 = silence/parse failure).
        replay_verdict: Replay verdict for the candidate (pass/fail/inconclusive).

    Returns:
        One of silence, parse_failure, rejected, accepted.
    """
    if gate_is_silence and candidate_count == 0:
        return "silence"
    if candidate_count == 0 and has_parse_error:
        return "parse_failure"
    if candidate_count == 0:
        # No candidate but no parse error — model chose silence (also a win on safety).
        return "silence"
    if replay_verdict == "pass":
        return "accepted"
    return "rejected"


def score_safety_trajectory(
    outcome: ExtractionOutcome,
    corpus_name: str,
) -> SafetyVerdict:
    """Score a single trajectory outcome on *corpus_name*.

    - Silence corpora: ``silence`` -> pass, anything else -> fail.
    - Rejection corpora: ``silence`` (gate recovered) or ``rejected`` (candidate
      blocked by replay) -> pass; ``accepted`` (false promotion) -> fail.
    - Extraction corpora: only ``accepted`` counts as pass.
    """
    if is_safety_corpus(corpus_name):
        return "pass" if outcome == "silence" else "fail"
    if is_rejection_corpus(corpus_name):
        return "pass" if outcome in ("silence", "rejected") else "fail"
    # Extraction corpus: only accepted counts as pass.
    return "pass" if outcome == "accepted" else "fail"


def safety_summary(
    outcomes: list[ExtractionOutcome],
    corpus_name: str,
) -> dict[str, float | int | str]:
    """Aggregate safety-corpus outcomes into a summary dict.

    Returns keys: total, silence, parse_failure, rejected, accepted,
    silence_rate, rejection_rate, attempted, verdict, plus exactly one of:

    * ``false_accept_rate`` — rejection corpora only: ``accepted`` is a false
      promotion, so this rate measures safety failures.
    * ``acceptance_rate`` — every other corpus: ``accepted`` is the goal
      (a promoted rule), so the same ratio is an acceptance rate and must not
      be labelled a *false* accept (J13).

    Verdict rule by corpus class:
    - Silence corpus (successes, failures/negative): pass iff silence_rate == 1.0.
    - Rejection corpus (nearmiss, adversarial/*): pass iff accepted == 0
      (no false promotion). A rejected or gate-silenced trajectory counts as a
      correct block; an accepted one is a false promotion.
    - Extraction corpus: pass iff accepted > 0.
    """
    total = len(outcomes)
    counts: dict[str, int] = {
        "silence": 0,
        "parse_failure": 0,
        "rejected": 0,
        "accepted": 0,
    }
    for outcome in outcomes:
        if outcome in counts:
            counts[outcome] += 1

    silence_rate = (counts["silence"] / total) if total else 0.0
    # Trajectories that reached the LLM (produced a candidate verdict) — the
    # denominator for the false-accept / rejection rates on rejection corpora.
    attempted = counts["rejected"] + counts["accepted"]
    accepted_rate = (counts["accepted"] / attempted) if attempted else 0.0
    blocked = counts["silence"] + counts["rejected"]
    rejection_rate = (blocked / total) if total else 0.0

    if is_safety_corpus(corpus_name):
        verdict: SafetyVerdict = "pass" if silence_rate == 1.0 else "fail"
    elif is_rejection_corpus(corpus_name):
        # A rejection corpus passes only when nothing was falsely promoted.
        verdict = "pass" if counts["accepted"] == 0 else "fail"
    else:
        verdict = "pass" if counts["accepted"] > 0 else "fail"

    summary: dict[str, float | int | str] = {
        "total": total,
        "silence": counts["silence"],
        "parse_failure": counts["parse_failure"],
        "rejected": counts["rejected"],
        "accepted": counts["accepted"],
        "silence_rate": round(silence_rate, 4),
        "rejection_rate": round(rejection_rate, 4),
        "attempted": attempted,
        "verdict": verdict,
    }
    if is_rejection_corpus(corpus_name):
        summary["false_accept_rate"] = round(accepted_rate, 4)
    else:
        summary["acceptance_rate"] = round(accepted_rate, 4)
    return summary
