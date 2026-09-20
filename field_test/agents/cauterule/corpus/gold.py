"""Gold rule families — curated trajectory-rule associations for evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field

from cauterule.models.rule import StandingRule
from cauterule.models.trajectory import Trajectory
from cauterule.serialization.trajectory_jsonl import load_trajectories


@dataclass(frozen=True)
class GoldRuleFamily:
    """A gold-standard scenario with trajectories and acceptable rules."""

    scenario_id: str
    trajectories: list[Trajectory] = field(default_factory=list)
    acceptable_rules: list[StandingRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.scenario_id.strip():
            raise ValueError("scenario_id must be non-blank")


def load_gold_families(path: str) -> list[GoldRuleFamily]:
    """Load gold rule families from a directory of JSONL files.

    Each JSONL file in *path* is loaded as a single gold family. The filename
    (without extension) becomes the ``scenario_id``.

    If a sidecar ``<scenario_id>.rules.yaml`` file exists alongside the JSONL,
    it is loaded as the family's ``acceptable_rules``.

    Args:
        path: Directory path containing ``*.jsonl`` files, one per family.

    Returns:
        List of :class:`GoldRuleFamily` instances.
    """
    from pathlib import Path

    from cauterule.serialization.rule_yaml import load_rule_from_file

    p = Path(path)
    if not p.is_dir():
        raise ValueError(f"Path must be a directory: {path}")

    families: list[GoldRuleFamily] = []
    for fpath in sorted(p.glob("*.jsonl")):
        trajectories = list(load_trajectories(fpath))
        scenario_id = fpath.stem
        sidecar = fpath.with_name(f"{scenario_id}.rules.yaml")
        acceptable_rules: list[StandingRule] = []
        if sidecar.is_file():
            acceptable_rules.append(load_rule_from_file(sidecar))
        families.append(
            GoldRuleFamily(
                scenario_id=scenario_id,
                trajectories=trajectories,
                acceptable_rules=acceptable_rules,
            )
        )
    return families
