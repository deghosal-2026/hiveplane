from __future__ import annotations

import statistics
from typing import Any

from planner_critic.reason_codes import DRIFT_ALERT_TRIGGERED
from planner_critic.types import Finding, Severity

SEVERITY_MAP: dict[Severity, int] = {Severity.BLOCKER: 2, Severity.WARNING: 1, Severity.INFO: 0}


def compute_drift(finding: Finding) -> Finding:
    """Compute drift_delta and normalized_severity for a finding.

    For LLM findings (heuristic_family is set), computes the drift between
    raw_severity and the finding's ``severity`` (which is the normalized
    value after guardrail enforcement). Stores raw_severity as the original
    value, normalized_severity as the current severity, and drift_delta as
    the difference.

    Args:
        finding: The finding to enrich with drift data.

    Returns:
        A new Finding with drift fields populated, or the original finding
        unchanged for deterministic gate findings.
    """
    if not finding.is_llm_finding:
        return finding

    raw = finding.raw_severity or finding.severity
    norm = finding.normalized_severity or finding.severity

    severity_map = SEVERITY_MAP
    raw_val = severity_map.get(raw, 1)
    norm_val = severity_map.get(norm, 1)
    drift_delta = norm_val - raw_val

    return finding.model_copy(
        update={
            "raw_severity": raw,
            "normalized_severity": norm,
            "drift_delta": drift_delta,
        }
    )


def compute_drift_summary(findings: list[Finding]) -> dict[str, Any]:
    """Compute a drift summary for a list of findings.

    **Measurement class (#231):** this summary measures critic-vs-guardrail
    disagreement — it only sees a defect when raw and normalized severity
    differ, i.e. when the guardrail overrode the critic. A critic that
    misclassifies family AND severity at origin produces raw == normalized,
    so every field here reads zero for it. ``critical_underclaims`` is
    therefore never a clean bill on its own; its fixed interpretation
    travels alongside it in ``critical_underclaims_interpretation``. For
    reality-keyed measurement (seeded known defects vs expected verdicts)
    use :mod:`planner_critic.eval.live_boundary`, and report the two
    together.

    Args:
        findings: The findings to analyze.

    Returns:
        A dict with drift metrics: total_findings, drifted_count,
        downgrade_rate, per_family, critical_underclaims, underclaim_findings,
        and ``critical_underclaims_interpretation``.
    """
    total = len(findings)
    drifted = [f for f in findings if f.drift_delta < 0]
    drifted_count = len(drifted)
    downgrade_rate = drifted_count / total if total > 0 else 0.0

    family_breakdown: dict[str, dict[str, int]] = {}
    for f in drifted:
        family = f.heuristic_family.value if f.heuristic_family else "unknown"
        if family not in family_breakdown:
            family_breakdown[family] = {"drifted": 0, "total": 0}
        family_breakdown[family]["drifted"] += 1
    for f in findings:
        family = f.heuristic_family.value if f.heuristic_family else "unknown"
        if family not in family_breakdown:
            family_breakdown[family] = {"drifted": 0, "total": 0}
        family_breakdown[family]["total"] += 1

    underclaims = [
        f
        for f in findings
        if f.raw_severity is not None
        and f.raw_severity is Severity.BLOCKER
        and f.normalized_severity is not None
        and SEVERITY_MAP.get(Severity.BLOCKER, 2) > SEVERITY_MAP.get(f.normalized_severity, 1)
        and f.heuristic_family
        and f.heuristic_family.value in ("risk", "missing_steps")
    ]

    return {
        "total_findings": total,
        "drifted_count": drifted_count,
        "downgrade_rate": downgrade_rate,
        "per_family": family_breakdown,
        "critical_underclaims": len(underclaims),
        "underclaim_findings": [f.id for f in underclaims],
        "critical_underclaims_interpretation": (
            "zero means the guardrail never overrode the critic — it says "
            "nothing about defects misclassified at origin (family and "
            "severity wrong from the start are invisible here). Pair with "
            "the live boundary runner (critic-vs-reality) before calling "
            "this a clean bill."
        ),
    }


def check_drift_alert(
    history: list[list[Finding]],
    family: str,
    z_threshold: float = 2.0,
) -> dict[str, Any]:
    """Check if a heuristic family's drift rate exceeds the z-score threshold.

    Args:
        history: List of findings lists, one per day (trailing 7-day window).
        family: The heuristic family to check.
        z_threshold: Number of standard deviations for alert (default 2.0).

    Returns:
        A dict with alert status, z_score, current_rate, mean_rate, std_dev,
        and reason_code if triggered.
    """
    if len(history) < 2:
        return {"alert": False, "reason": "insufficient_history"}

    rates: list[float] = []
    for day_findings in history:
        total = len(day_findings)
        drifted = sum(1 for f in day_findings if f.drift_delta != 0)
        rates.append(drifted / total if total > 0 else 0.0)

    mean_rate = statistics.mean(rates)
    std_dev = statistics.stdev(rates) if len(rates) > 1 else 0.0
    current_rate = rates[-1]

    if std_dev == 0.0:
        z_score = 0.0
    else:
        z_score = (current_rate - mean_rate) / std_dev

    alert = z_score > z_threshold

    return {
        "alert": alert,
        "z_score": z_score,
        "current_rate": current_rate,
        "mean_rate": mean_rate,
        "std_dev": std_dev,
        "family": family,
        "reason_code": DRIFT_ALERT_TRIGGERED if alert else None,
    }


__all__ = [
    "check_drift_alert",
    "compute_drift",
    "compute_drift_summary",
]
