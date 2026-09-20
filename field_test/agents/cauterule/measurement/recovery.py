"""Fix 8 recovery-exclusion re-run (#491; plan §5.6).

v0.1.0's "Fix 8" made the pre-extraction gate exclude trajectories where a
failure was *recovered* (near-miss retry succeeded) from rule extraction. That
was measured on cloud models only; v0.3.0 re-runs the exclusion over the
recovery/near-miss corpora to confirm it still holds on the local OMLX tier.

The exclusion is a gate-side (pre-LLM) property, so the re-run is hermetic: run
the gate over the recovery corpora and measure how often a ``should_reject`` /
``should_silence`` (recovered) trajectory is correctly silenced.
"""

from __future__ import annotations

from dataclasses import dataclass

RECOVERY_EXCLUSION_TARGET = 0.50
"""Minimum share of recovered trajectories that must be excluded from extraction."""


@dataclass(frozen=True)
class RecoveryExclusionReport:
    """Recovery-exclusion outcome for a corpus."""

    recovered_expected: int
    excluded: int
    extracted: int
    exclusion_rate: float
    meets_target: bool

    @property
    def false_extractions(self) -> int:
        """Recovered trajectories that were wrongly extracted (not excluded)."""
        return self.extracted


def recovery_exclusion(
    records: list[dict[str, object]],
    *,
    target: float = RECOVERY_EXCLUSION_TARGET,
) -> RecoveryExclusionReport:
    """Measure how often recovered trajectories are correctly gate-excluded.

    *records* carry ``expected_outcome`` and ``gate_is_silence``. Only
    ``should_reject`` / ``should_silence`` records count as recovered-expected
    (a clean success carries no recovery signal and is not a Fix 8 case).
    """
    recovered = [
        r for r in records if str(r.get("expected_outcome")) in ("should_reject", "should_silence")
    ]
    excluded = sum(1 for r in recovered if r.get("gate_is_silence") is True)
    extracted = len(recovered) - excluded
    rate = excluded / len(recovered) if recovered else 0.0
    return RecoveryExclusionReport(
        recovered_expected=len(recovered),
        excluded=excluded,
        extracted=extracted,
        exclusion_rate=rate,
        meets_target=bool(recovered) and rate >= target,
    )
