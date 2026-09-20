"""Small statistics helpers for field-test reporting (#695).

Field-test metrics are point estimates from small, unreplicated samples
(n=10 golden, n=50 nearmiss, n=10 per adversarial vector).  Reporting a bare
percentage overstates precision.  :func:`wilson_ci` provides the Wilson score
interval, which behaves correctly for proportions near 0%/100% (unlike the
normal approximation).
"""

from __future__ import annotations

import math
from statistics import NormalDist

_DEFAULT_CONFIDENCE = 0.95


def wilson_ci(
    successes: int,
    total: int,
    confidence: float = _DEFAULT_CONFIDENCE,
) -> tuple[float, float]:
    """Return the Wilson score interval for ``successes / total``.

    Args:
        successes: Number of successes (0 <= successes <= total).
        total: Number of trials.
        confidence: Confidence level in (0, 1), e.g. 0.95.

    Returns:
        ``(low, high)`` clamped to [0.0, 1.0].  With ``total == 0`` there is
        no data, so the maximally-uncertain interval ``(0.0, 1.0)`` is
        returned.
    """
    if total < 0 or successes < 0 or successes > total:
        raise ValueError(f"invalid counts: successes={successes}, total={total}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if total == 0:
        return (0.0, 1.0)

    z = NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    phat = successes / total
    denom = 1.0 + z * z / total
    center = (phat + z * z / (2.0 * total)) / denom
    margin = z * math.sqrt(phat * (1.0 - phat) / total + z * z / (4.0 * total * total)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def rate_with_ci(
    successes: int,
    total: int,
    confidence: float = _DEFAULT_CONFIDENCE,
) -> dict[str, float | int]:
    """Return ``{rate, n, ci_low, ci_high}`` for a proportion."""
    rate = successes / total if total else 0.0
    low, high = wilson_ci(successes, total, confidence=confidence)
    return {
        "rate": round(rate, 4),
        "n": total,
        "ci_low": round(low, 4),
        "ci_high": round(high, 4),
    }
