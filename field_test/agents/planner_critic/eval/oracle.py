"""Security oracle eval harness for M5 (Security & Trust Oracle).

The harness runs a planner-critic loop against each corpus instance,
classifies every critic finding as aligned / missed / spurious against the
ground-truth patch, and produces a scorecard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ..corpus import load_all_instances
from ..corpus.types import SecurityInstance
from ..engine import Engine
from ..loop import LoopConfig
from ..reason_codes import ReasonCode
from ..roles import CriticRole, PlannerRole
from ..schema.goal import Goal, RiskTolerance
from ..types import Finding, Severity


@dataclass
class InstanceResult:
    """Result of evaluating a single corpus instance."""

    instance_id: str
    cwe: str
    cwe_bucket: str
    aligned: list[Finding] = field(default_factory=list)
    missed: list[Finding] = field(default_factory=list)
    spurious: list[Finding] = field(default_factory=list)
    plan_tasks: list[dict[str, object]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def aligned_count(self) -> int:
        return len(self.aligned)

    @property
    def missed_count(self) -> int:
        return len(self.missed)

    @property
    def spurious_count(self) -> int:
        return len(self.spurious)


@dataclass
class EvalScorecard:
    """Aggregate evaluation results across all instances."""

    total_instances: int = 0
    total_aligned: int = 0
    total_missed: int = 0
    total_spurious: int = 0
    per_cwe: dict[str, dict[str, int]] = field(default_factory=dict)
    accuracy: float = 0.0

    def compute(self) -> None:
        total = self.total_aligned + self.total_missed
        self.accuracy = self.total_aligned / total if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_instances": self.total_instances,
            "total_aligned": self.total_aligned,
            "total_missed": self.total_missed,
            "total_spurious": self.total_spurious,
            "accuracy": round(self.accuracy, 4),
            "per_cwe": self.per_cwe,
        }


class OracleEvalHarness:
    """Evaluates a planner-critic pair against the security corpus.

    Args:
        planner: The planner role to evaluate.
        critic: The critic role to evaluate.
        corpus_dir: Path to the security corpus directory.
        loop_config: Loop configuration (defaults to deterministic-first).
    """

    def __init__(
        self,
        planner: PlannerRole,
        critic: CriticRole,
        corpus_dir: str | Path = "docs/field-test/corpus/swebench-security",
        loop_config: LoopConfig | None = None,
    ) -> None:
        self.planner = planner
        self.critic = critic
        self.corpus_dir = Path(corpus_dir)
        self.loop_config = loop_config or LoopConfig(mode="deterministic-first")

    def run_instance(self, instance: SecurityInstance) -> InstanceResult:
        """Run the planner-critic loop on a single corpus instance.

        Classifies every finding as aligned, missed, or spurious based on
        whether the expected critic signal matches the finding's heuristic
        family.
        """
        goal = Goal(
            id=instance.instance_id,
            description=instance.goal_text,
            risk_tolerance=RiskTolerance.BALANCED,
        )

        engine = Engine(
            planner=self.planner,
            critic=self.critic,
            config=self.loop_config,
        )

        result = engine.plan(goal)
        expected_codes = set(instance.expected_reason_codes)

        aligned: list[Finding] = []
        missed: list[Finding] = []
        spurious: list[Finding] = []
        covered_expected_codes: set[str] = set()

        for f in result.findings:
            if isinstance(f.reason_code, str) and f.reason_code in expected_codes:
                aligned.append(f)
                covered_expected_codes.add(f.reason_code)
            elif f.is_llm_finding and instance.expected_critic_signal is not None:
                expected_signal = instance.expected_critic_signal.value
                if f.heuristic_family and f.heuristic_family.value == expected_signal:
                    aligned.append(f)
                else:
                    spurious.append(f)
            else:
                spurious.append(f)

        for code in expected_codes:
            if code in covered_expected_codes:
                continue
            if not any(
                isinstance(f.reason_code, str) and f.reason_code == code for f in result.findings
            ):
                missed.append(
                    Finding(
                        id=f"missed:{instance.instance_id}:{code}",
                        version=result.findings[0].version if result.findings else 1,
                        severity=Severity.WARNING,
                        reason_code=cast(ReasonCode, code),
                        message=f"Expected finding for {code} not produced",
                    )
                )

        return InstanceResult(
            instance_id=instance.instance_id,
            cwe=instance.cwe,
            cwe_bucket=instance.cwe_bucket.value,
            aligned=aligned,
            missed=missed,
            spurious=spurious,
            plan_tasks=(
                [{"id": t.id, "action": t.action, "target": t.target} for t in result.plan.tasks]
                if result.plan
                else []
            ),
            metadata={
                "expected_signal": (
                    instance.expected_critic_signal.value
                    if instance.expected_critic_signal
                    else None
                ),
            },
        )

    def run_all(
        self,
        instance_ids: list[str] | None = None,
    ) -> tuple[EvalScorecard, list[InstanceResult]]:
        """Run all instances (or a subset) and produce the aggregate scorecard."""
        instances = load_all_instances(str(self.corpus_dir))
        if instance_ids:
            instances = [i for i in instances if i.instance_id in instance_ids]

        scorecard = EvalScorecard(total_instances=len(instances))
        results: list[InstanceResult] = []

        for inst in instances:
            result = self.run_instance(inst)
            results.append(result)
            cwe_bucket = result.cwe_bucket
            if cwe_bucket not in scorecard.per_cwe:
                scorecard.per_cwe[cwe_bucket] = {"aligned": 0, "missed": 0, "spurious": 0}
            scorecard.total_aligned += result.aligned_count
            scorecard.total_missed += result.missed_count
            scorecard.total_spurious += result.spurious_count
            scorecard.per_cwe[cwe_bucket]["aligned"] += result.aligned_count
            scorecard.per_cwe[cwe_bucket]["missed"] += result.missed_count
            scorecard.per_cwe[cwe_bucket]["spurious"] += result.spurious_count

        scorecard.compute()
        return scorecard, results


def save_report(
    scorecard: EvalScorecard,
    output_path: str | Path,
    results: list[InstanceResult] | None = None,
) -> None:
    """Save the eval scorecard and optional per-instance results to disk.

    Args:
        scorecard: The aggregate scorecard.
        output_path: Target file path (JSON).
        results: Optional per-instance results to include.
    """
    report = scorecard.to_dict()
    if results:
        report["instances"] = [
            {
                "instance_id": r.instance_id,
                "cwe": r.cwe,
                "cwe_bucket": r.cwe_bucket,
                "aligned_count": r.aligned_count,
                "missed_count": r.missed_count,
                "spurious_count": r.spurious_count,
            }
            for r in results
        ]

    output_path = Path(output_path)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


__all__ = [
    "EvalScorecard",
    "InstanceResult",
    "OracleEvalHarness",
    "save_report",
]
