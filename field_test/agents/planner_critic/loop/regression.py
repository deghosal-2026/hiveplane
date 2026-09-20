"""The regression guard (PRD §2.6(d)) — a revision introduced a new blocker.

The planner is thrashing when a revision *adds* a blocker that did not exist
in the prior revision. Escalating here stops an unproductive loop and keeps
the audit trail honest.

**Deterministic only (F-12):** regression is measured on *deterministic gate*
blockers, not LLM critic blockers. Gate blockers are reproducible — if a new
one appears, the planner genuinely regressed. LLM blocker findings are
probabilistic and may vary across audits of the same plan, so treating their
appearance as "regression" produces false thrashing signals (§2.5.1).
"""

from __future__ import annotations

from ..types import Finding, Severity


def _gate_blocker_keys(findings: list[Finding]) -> set[tuple[str, str | None]]:
    """Blocker keys from deterministic gates only (no LLM findings)."""
    return {
        (f.reason_code, f.task_id)
        for f in findings
        if f.severity is Severity.BLOCKER and not f.is_llm_finding
    }


def regression_detected(prior: list[Finding], current: list[Finding]) -> bool:
    """True when the current revision regressed vs the prior one.

    "Introduces a new blocker" ⟺ a deterministic-gate blocker reason-code/task
    pair appears in ``current`` that was absent from ``prior``. LLM critic
    blockers are excluded from this check because they are non-deterministic.

    Args:
        prior: Findings from the previous revision.
        current: Findings from the current revision.

    Returns:
        True when ``current`` has a gate blocker key that ``prior`` did not.
    """
    if not prior:
        return False
    prior_keys = _gate_blocker_keys(prior)
    current_keys = _gate_blocker_keys(current)
    return bool(current_keys.difference(prior_keys))
