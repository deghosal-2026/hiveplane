"""Outcome simulator."""

from __future__ import annotations

import re
from typing import Literal

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory
from cauterule.replay.matcher import (
    DEFAULT_THRESHOLD,
    check_domain_mismatch,
    is_near_miss,
    rule_matches_with_score,
)

Outcome = Literal["prevented", "broken", "no_effect", "near_miss"]

# Recovery signals matched as EXACT slash-delimited taxonomy segments.
# Substring matching here caused semantic inversion (#616): "unrecoverable"
# matched "recover", "retry budget exhausted" matched "retry", "template"
# matched "temp", and every "nearmiss/*" label circularly matched "near".
# Bare "temp" is excluded (temperature/template collide); "nearmiss" is
# excluded (corpus category label, not a recovery signal — see #616).
RECOVERY_CLASS_TOKENS = frozenset(
    {
        "temporary",
        "retry",
        "recovered",
        "recovery",
        "intermittent",
        "flaky",
        "near_miss",
    }
)

# #723: a success must clear the threshold by this margin before it counts as
# "broken". A success matched barely above threshold is coincidence, not
# interference (e.g. a "git push" rule vs a "git status" success sharing only
# the token "git").
BROKEN_MARGIN = 0.10

# #723: recovery indicators in a success's id/task (e.g. the NM-* recovered
# failure references). Deliberately NOT applied to failure_class — that path is
# whole-segment (#616) and token-matching it would revive the "retry budget
# exhausted" inversion.
_RECOVERY_TASK_TOKENS = frozenset(
    {
        "retry",
        "retries",
        "retried",
        "retrying",
        "rollback",
        "recover",
        "recovered",
        "recovery",
        "flaky",
        "intermittent",
        "temporary",
        "fallback",
        "transient",
    }
)


def is_recovery_class(failure_class: str | None) -> bool:
    """Return True if *failure_class* denotes a genuine recovery.

    Matches whole ``/``-separated segments only — never substrings.
    Non-string inputs (possible from unvalidated JSONL) return False.
    """
    if not isinstance(failure_class, str):
        return False
    segments = [seg.strip().lower() for seg in failure_class.split("/")]
    return any(seg in RECOVERY_CLASS_TOKENS for seg in segments)


def _has_recovery_token(*texts: str | None) -> bool:
    for text in texts:
        if not text:
            continue
        tokens = set(re.split(r"[^a-z0-9]+", text.lower()))
        if tokens & _RECOVERY_TASK_TOKENS:
            return True
    return False


def is_recovery_trajectory(trajectory: Trajectory) -> bool:
    """Return True if a success looks like a recovered/near-miss (#723).

    Combines the whole-segment ``failure_class`` check (#616) with a token
    check on the trajectory ``id``/``task`` so ``NM-*-retry`` / ``NM-*-rollback``
    recovered successes classify as ``near_miss`` rather than ``broken``.
    """
    if is_recovery_class(trajectory.failure_class):
        return True
    return _has_recovery_token(trajectory.id, trajectory.task)


def simulate(
    candidate: CandidateRule, trajectory: Trajectory, threshold: float = DEFAULT_THRESHOLD
) -> Outcome:
    """Simulate whether *candidate* would change *trajectory* outcome.

    The verdict is a pure function of ``when`` (trigger/context/signature) and
    the trajectory text: ``do.directive`` is **not** read, so two candidates
    with an identical trigger but different directives yield identical
    outcomes (#762). Directive-aware grounding is Phase 1 of #762 / #720.

    Args:
        candidate: The candidate rule to test.
        trajectory: The trajectory to test against.
        threshold: Matcher threshold (default 0.6). Use corpus-aware
            :func:`threshold_for_corpus` for per-corpus tuning.

    Returns:
        - ``prevented`` if failure and matches (would have prevented)
        - ``broken`` if success and matches (would break success)
        - ``near_miss`` if partial context match or recovery trajectory
        - ``no_effect`` otherwise
    """
    if is_near_miss(candidate, trajectory, threshold):
        return "near_miss"
    # Cross-domain matches on failures are already rejected inside
    # rule_matches_with_score (#487); the score is returned for the margin check.
    matched, score = rule_matches_with_score(candidate, trajectory, threshold)
    if not matched:
        return "no_effect"
    if not trajectory.success:
        return "prevented"
    # Success path: recovery/near-miss classification first (domain-independent,
    # #616), then the cross-domain downgrade (#723), then a match-strength margin.
    if is_recovery_trajectory(trajectory):
        return "near_miss"
    if check_domain_mismatch(candidate, trajectory):
        return "no_effect"
    if score < threshold + BROKEN_MARGIN:
        return "no_effect"
    return "broken"
