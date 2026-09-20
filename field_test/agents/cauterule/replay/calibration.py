"""Threshold calibration helpers (#691).

The matcher's threshold constants were previously hand-picked with no
provenance and no fast signal that a change degraded precision/recall.  This
module loads a fixed, checked-in labeled sample derived from the public
``golden`` and ``nearmiss`` corpora and evaluates the real production matcher
(:func:`cauterule.replay.matcher.rule_matches`) at candidate thresholds.

* ``scripts/calibrate_thresholds.py`` sweeps the sample and renders the
  committed evidence table (``docs/field-test/v0.3.0/threshold-calibration.md``).
* ``tests/replay/test_threshold_calibration.py`` asserts the shipped
  thresholds keep precision/recall above a baseline, catching a silent
  threshold/alias regression in CI instead of in a multi-hour field sweep.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen
from cauterule.models.trajectory import Trajectory
from cauterule.replay.matcher import rule_matches

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PUBLIC_ROOT = _REPO_ROOT / "corpus" / "public"


@dataclass(frozen=True)
class CalibrationPair:
    """A labeled (rule, trajectory) pair for calibration."""

    rule_id: str
    trajectory_id: str
    candidate: CandidateRule
    trajectory: Trajectory
    expected_match: bool


@dataclass(frozen=True)
class CalibrationMetrics:
    """Confusion-matrix metrics at a given threshold."""

    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float:
        """Correct matches as a fraction of all predicted matches."""
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        """Correct matches as a fraction of all expected matches."""
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0


def _load_trajectory(path: Path) -> Trajectory:
    first = path.read_text(encoding="utf-8").strip().splitlines()[0]
    return Trajectory.from_dict(json.loads(first))


def _load_candidate(path: Path) -> CandidateRule:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    when = data.get("when", {})
    do = data.get("do", {})
    return CandidateRule(
        when=RuleWhen(trigger=when["trigger"], context=tuple(when.get("context", []))),
        do=RuleDo(directive=do["directive"]),
        confidence=float(data.get("confidence", 0.9)),
    )


def load_calibration_pairs(public_root: str | Path | None = None) -> list[CalibrationPair]:
    """Load the labeled golden/nearmiss calibration sample.

    Positives: each golden rule against its own scenario trajectory.
    Negatives: each golden rule against every other golden scenario and every
    nearmiss trajectory (cross-scenario and near-miss false-positive probes).
    """
    root = Path(public_root) if public_root is not None else DEFAULT_PUBLIC_ROOT
    golden_dir = root / "golden"
    nearmiss_dir = root / "nearmiss"

    scenarios: dict[str, Trajectory] = {
        p.stem: _load_trajectory(p) for p in sorted(golden_dir.glob("*.jsonl"))
    }
    rules: list[tuple[str, CandidateRule]] = []
    for scenario_id in scenarios:
        for rule_path in sorted(golden_dir.glob(f"{scenario_id}-*.yaml")):
            rules.append((rule_path.stem, _load_candidate(rule_path)))
    nearmiss = [_load_trajectory(p) for p in sorted(nearmiss_dir.glob("*.jsonl"))]

    pairs: list[CalibrationPair] = []
    for rule_id, candidate in rules:
        scenario_id = rule_id.rsplit("-", 1)[0]
        own = scenarios.get(scenario_id)
        if own is not None:
            pairs.append(CalibrationPair(rule_id, own.id, candidate, own, expected_match=True))
        for other_id, traj in scenarios.items():
            if other_id != scenario_id:
                pairs.append(
                    CalibrationPair(rule_id, traj.id, candidate, traj, expected_match=False)
                )
        for traj in nearmiss:
            pairs.append(CalibrationPair(rule_id, traj.id, candidate, traj, expected_match=False))
    return pairs


def evaluate_threshold(
    threshold: float, pairs: list[CalibrationPair] | None = None
) -> CalibrationMetrics:
    """Evaluate the production matcher at *threshold* over the sample."""
    data = pairs if pairs is not None else load_calibration_pairs()
    tp = fp = fn = tn = 0
    for pair in data:
        matched = rule_matches(pair.candidate, pair.trajectory, threshold=threshold)
        if pair.expected_match and matched:
            tp += 1
        elif pair.expected_match and not matched:
            fn += 1
        elif not pair.expected_match and matched:
            fp += 1
        else:
            tn += 1
    return CalibrationMetrics(threshold=threshold, tp=tp, fp=fp, fn=fn, tn=tn)


def sweep_thresholds(
    start: float = 0.50,
    stop: float = 0.80,
    step: float = 0.05,
    pairs: list[CalibrationPair] | None = None,
) -> list[CalibrationMetrics]:
    """Evaluate precision/recall at every threshold in ``[start, stop]``."""
    data = pairs if pairs is not None else load_calibration_pairs()
    results: list[CalibrationMetrics] = []
    t = start
    while t <= stop + 1e-9:
        results.append(evaluate_threshold(round(t, 2), data))
        t += step
    return results
