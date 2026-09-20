"""Pack replay scoring (#479/#481; plan §5.3/§7.2).

Replays each official pack's rules against the pack-replay corpus and computes:

    pack_prevented = # rule matches on should_extract trajectories
    pack_broke     = # rule matches on should_silence trajectories
    pack_score     = prevented / (prevented + broke)   if any matches else 0

Target (§6.1): the four official packs replay with ≥1 prevented failure each and
``pack_score ≥ 0.5``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cauterule.models.candidate import CandidateRule

PACK_SCORE_TARGET = 0.50
DEFAULT_MATCH_THRESHOLD = 0.70


@dataclass(frozen=True)
class PackReplayReport:
    """Replay outcome for one pack."""

    pack: str
    rules: int
    trajectories: int
    prevented: int
    broke: int
    neutral: int
    score: float
    matches: list[dict[str, object]] = field(default_factory=list)

    @property
    def meets_target(self) -> bool:
        """True when the pack prevents ≥1 failure and scores ≥0.5."""
        return self.prevented >= 1 and self.score >= PACK_SCORE_TARGET


def _as_float(value: object, default: float) -> float:
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


def _candidate(rule: dict[str, object]) -> CandidateRule:
    from cauterule.models.candidate import CandidateRule
    from cauterule.models.rule import RuleDo, RuleWhen

    return CandidateRule(
        when=RuleWhen(trigger=str(rule.get("trigger", ""))),
        do=RuleDo(directive=str(rule.get("directive", "Apply the pack rule"))),
        confidence=_as_float(rule.get("confidence"), 0.9),
    )


def pack_replay_score(
    pack: str,
    rules: list[dict[str, object]],
    trajectories: list[dict[str, object]],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> PackReplayReport:
    """Score *rules* against *trajectories* for pack *pack*.

    A rule match on a ``should_extract`` trajectory counts as *prevented*; a
    match on any other expected outcome (``should_silence``/``should_reject``)
    counts as *broke*. Each trajectory is counted once even if multiple rules
    match.
    """
    from cauterule.models.trajectory import Trajectory
    from cauterule.replay.matcher import match_score

    candidates = [(_candidate(rule), str(rule.get("id", "?"))) for rule in rules]
    traj_objs: list[tuple[Trajectory, str]] = []
    for record in trajectories:
        try:
            traj_objs.append(
                (Trajectory.from_dict(record), str(record.get("expected_outcome", "")))
            )
        except Exception:
            continue

    prevented = 0
    broke = 0
    neutral = 0
    matches: list[dict[str, object]] = []
    for traj_obj, outcome in traj_objs:
        best_rule: str | None = None
        best_score = 0.0
        for candidate, rule_id in candidates:
            score = match_score(candidate, traj_obj)
            if score > best_score:
                best_score = score
                best_rule = rule_id
        if best_rule is not None and best_score >= threshold:
            matched = outcome == "should_extract"
            if matched:
                prevented += 1
            else:
                broke += 1
            matches.append(
                {
                    "trajectory_id": traj_obj.id,
                    "rule_id": best_rule,
                    "score": round(best_score, 4),
                    "outcome": outcome,
                    "kind": "prevented" if matched else "broke",
                }
            )
        else:
            neutral += 1

    total_matched = prevented + broke
    score = (prevented / total_matched) if total_matched else 0.0
    return PackReplayReport(
        pack=pack,
        rules=len(rules),
        trajectories=len(traj_objs),
        prevented=prevented,
        broke=broke,
        neutral=neutral,
        score=score,
        matches=matches,
    )
