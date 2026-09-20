"""Per-rule outcome tracking (prevented / broke / neutral over time) (#542).

Records every replay/injection outcome attributed to a rule and persists:

- inline counters on the rule (``prevented_count``, ``broke_count``,
  ``neutral_count``) for cheap reads in list/metrics,
- ``last_outcome`` / ``last_outcome_at`` for staleness policy,
- a capped ``outcome_trend`` (trailing N outcomes, +1/0/-1) for sparkline UI,
- an append-only JSONL log at ``<base_dir>/outcomes/<rule-id>.jsonl`` keyed
  by ``(trajectory_id, rule_id)`` to keep long histories out of the rule YAML
  hot path and to make recording idempotent (replay-cache hits never
  double-count).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cauterule.models.evidence import EvidenceReport
from cauterule.models.rule import StandingRule
from cauterule.store.manager import StoreManager

# Valid outcome labels.
OUTCOMES = ("prevented", "broke", "neutral")
_TREND_CAP = 50

# Trend values: prevented=+1, neutral=0, broke=-1.
_TREND_VALUE = {"prevented": 1, "neutral": 0, "broke": -1}


def _trend_path(base_dir: Path, rule_id: str) -> Path:
    return base_dir / "outcomes" / f"{rule_id}.jsonl"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_exists(base_dir: Path, rule_id: str, trajectory_id: str) -> bool:
    """Return True if *trajectory_id* was already recorded for *rule_id*."""
    path = _trend_path(base_dir, rule_id)
    marker = f'"{trajectory_id}"'
    if not path.is_file():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and marker in line:
                return True
    except OSError:
        return False
    return False


def _append_log(
    base_dir: Path,
    rule_id: str,
    trajectory_id: str,
    outcome: str,
    timestamp: str,
) -> None:
    path = _trend_path(base_dir, rule_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "trajectory_id": trajectory_id,
        "rule_id": rule_id,
        "outcome": outcome,
        "recorded_at": timestamp,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_outcome(
    store: StoreManager,
    rule_id: str,
    outcome: str,
    *,
    trajectory_id: str = "",
    timestamp: str | None = None,
) -> StandingRule:
    """Record a single outcome for *rule_id*; return the updated rule.

    Idempotency: when *trajectory_id* is non-empty and already present in
    the outcome log for this rule, no double count is applied (guards
    replay-cache hits re-recording).

    Args:
        store: Rule store.
        rule_id: Rule to record against.
        outcome: ``"prevented"``, ``"broke"``, or ``"neutral"``.
        trajectory_id: Id of the trajectory that produced the outcome.
        timestamp: ISO8601 timestamp; defaults to now UTC.

    Returns:
        The updated :class:`StandingRule`.

    Raises:
        ValueError: If the rule does not exist or *outcome* is invalid.
    """
    if outcome not in OUTCOMES:
        msg = f"outcome must be one of {OUTCOMES}, got {outcome!r}"
        raise ValueError(msg)
    rule = store.get_rule(rule_id)
    if rule is None:
        msg = f"Rule {rule_id!r} not found"
        raise ValueError(msg)

    if trajectory_id and _log_exists(store.base_dir, rule_id, trajectory_id):
        return rule  # already recorded — no double count

    ts = timestamp or _now()
    prevented = rule.prevented_count + (1 if outcome == "prevented" else 0)
    broke = rule.broke_count + (1 if outcome == "broke" else 0)
    neutral = rule.neutral_count + (1 if outcome == "neutral" else 0)
    trend = list(rule.outcome_trend[-(_TREND_CAP - 1) :])
    trend.append(_TREND_VALUE[outcome])

    updated = replace(
        rule,
        prevented_count=prevented,
        broke_count=broke,
        neutral_count=neutral,
        last_outcome=outcome,
        last_outcome_at=ts,
        outcome_trend=tuple(trend),
    )
    # Refresh the stored specificity score (#541): it feeds on the counters
    # just updated, and lifecycling (retirement/promotion) reads it cheaply.
    try:
        from cauterule.lifecycle.specificity import compute_specificity

        spec, inputs = compute_specificity(updated)
        updated = replace(updated, specificity=spec, specificity_inputs=inputs)
    except Exception:
        pass  # specificity is advisory; never block outcome recording
    store.add_rule(updated)
    if trajectory_id:
        _append_log(store.base_dir, rule_id, trajectory_id, outcome, ts)
    return updated


def apply_report_outcomes(
    store: StoreManager,
    rule_id: str,
    report: EvidenceReport,
    *,
    timestamp: str | None = None,
) -> dict[str, int]:
    """Record the per-trajectory outcomes from an :class:`EvidenceReport`.

    Prepended/broken/near-miss trajectory ids from ``report.replay_trace``
    are mapped to rule outcomes and recorded individually so each
    ``(trajectory, rule)`` pair lands in the idempotent log.

    Args:
        store: Rule store.
        rule_id: The standing rule whose outcomes derive from *report*.
        report: An :class:`cauterule.models.evidence.EvidenceReport`.
        timestamp: ISO8601 timestamp override.

    Returns:
        Dict mapping rule_id to its new outcome counters.
    """
    counts = {"prevented": 0, "broke": 0, "neutral": 0}
    seen: set[str] = set()
    for trace in report.replay_trace:
        traj_id = str(trace.get("trajectory_id", ""))
        if not traj_id or traj_id in seen:
            continue
        seen.add(traj_id)
        outcome = trace.get("outcome")
        if outcome == "prevented":
            counts["prevented"] += 1
            record_outcome(store, rule_id, "prevented", trajectory_id=traj_id, timestamp=timestamp)
        elif outcome == "broken":
            counts["broke"] += 1
            record_outcome(store, rule_id, "broke", trajectory_id=traj_id, timestamp=timestamp)
        elif outcome in ("no_effect", "near_miss", None):
            counts["neutral"] += 1
            record_outcome(store, rule_id, "neutral", trajectory_id=traj_id, timestamp=timestamp)
    return counts


def rule_outcome_summary(store: StoreManager, rule_id: str) -> dict[str, Any]:
    """Return the outcome summary for *rule_id* (counters + trend)."""
    rule = store.get_rule(rule_id)
    if rule is None:
        msg = f"Rule {rule_id!r} not found"
        raise ValueError(msg)
    total = rule.prevented_count + rule.broke_count + rule.neutral_count
    prevented_rate = rule.prevented_count / total if total else 0.0
    return {
        "rule_id": rule_id,
        "prevented": rule.prevented_count,
        "broke": rule.broke_count,
        "neutral": rule.neutral_count,
        "last_outcome": rule.last_outcome,
        "last_outcome_at": rule.last_outcome_at,
        "trend": list(rule.outcome_trend),
        "prevented_rate": round(prevented_rate, 4),
    }


def sparkline(trend: tuple[int, ...]) -> str:
    """Render *trend* as a compact sparkline (▁▄█ style, up=█ down=▁)."""
    chars = {-1: "▁", 0: "▄", 1: "█"}
    return "".join(chars.get(v, "·") for v in trend)
