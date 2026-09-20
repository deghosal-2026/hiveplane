"""Evidence scorer with broad-trigger and near-miss penalty."""

from __future__ import annotations

from cauterule.models.evidence import Verdict


def compute_scores_detailed(
    prevented: int,
    broken: int,
    total_failures: int,
    total_successes: int,
    near_misses: int = 0,
) -> tuple[float, float, Verdict, str]:
    """Compute precision, recall, verdict, and a machine-readable reason.

    Ordering (#724):
    1. No signal (nothing matched) -> inconclusive.
    2. Dangerously broad (``broken > prevented``) -> fail. This is the only
       hard-fail guard: a rule that breaks more successes than it prevents.
    3. Over-broad near-miss (``near_misses > 2``) -> inconclusive.
    4. Otherwise -> pass. Because ``broken <= prevented`` mathematically implies
       ``precision >= 0.5``, a net-positive rule passes even when it touches a
       few successes (the old ``broken > 0 -> inconclusive`` guard was removed).

    Args:
        prevented: Failures prevented.
        broken: Successes broken.
        total_failures: Total failures in corpus.
        total_successes: Total successes in corpus.
        near_misses: Near-miss references matched (over-broad trigger).

    Returns:
        ``(precision, recall, verdict, reason)`` where reason is one of
        ``pass``, ``no_signal``, ``blocked_by_broken``, ``blocked_by_near_miss``.
    """
    denom = prevented + broken
    precision = (prevented / denom) if denom else 0.0
    recall = (prevented / total_failures) if total_failures > 0 else 0.0

    if prevented == 0 and broken == 0:
        return precision, recall, "inconclusive", "no_signal"
    if broken > prevented:
        return precision, recall, "fail", "blocked_by_broken"
    if near_misses > 2:
        return precision, recall, "inconclusive", "blocked_by_near_miss"
    # broken <= prevented implies precision >= 0.5 -> net-positive pass.
    return precision, recall, "pass", "pass"


def compute_scores(
    prevented: int,
    broken: int,
    total_failures: int,
    total_successes: int,
    near_misses: int = 0,
) -> tuple[float, float, Verdict]:
    """Compute precision, recall, verdict.

    Broad-trigger penalty: if a trigger matches successes it would break,
    it is too broad. Triggers that break more successes than they prevent
    failures are "fail".

    Near-miss penalty: if a trigger also matches near-miss references
    (recovered or ambiguous trajectories), it is over-broad. A candidate
    with ``near_misses > 2`` is downgraded from ``pass`` to ``inconclusive``
    — the trigger fires on trajectories that should not have produced a
    rule (v0.3.0 field-test fix: nearmiss corpus false passes at precision
    1.0 because near-misses were computed but never penalised).

    v0.3.1 field-test tuning (#724): a net-positive rule (``broken <=
    prevented``, ``precision >= 0.5``) passes even when it touches a few
    successes; ``fail`` is reserved for ``broken > prevented``.

    Args:
        prevented: Failures prevented.
        broken: Successes broken.
        total_failures: Total failures in corpus.
        total_successes: Total successes in corpus.
        near_misses: Near-miss references matched (over-broad trigger).

    Returns:
        (precision, recall, verdict) where verdict is pass/fail/inconclusive.
    """
    precision, recall, verdict, _reason = compute_scores_detailed(
        prevented=prevented,
        broken=broken,
        total_failures=total_failures,
        total_successes=total_successes,
        near_misses=near_misses,
    )
    return precision, recall, verdict
