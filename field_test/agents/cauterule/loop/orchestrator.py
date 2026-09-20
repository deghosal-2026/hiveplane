"""Loop orchestrator — wires capture → redact → cluster → extract → lint → replay → tournament → conflict → promote → inject."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cauterule.extraction.multipass import multipass_extract
from cauterule.extraction.tournament import run_tournament
from cauterule.linter.orchestrator import LinterResult, lint_rule
from cauterule.models.candidate import CandidateRule
from cauterule.models.evidence import EvidenceReport
from cauterule.models.rule import StandingRule
from cauterule.models.trajectory import Trajectory
from cauterule.promotion import auto_promote, execute_promotion
from cauterule.redaction.engine import redact_trajectory
from cauterule.replay.report import build_evidence_report
from cauterule.security import is_source_tainted


@dataclass(frozen=True)
class LoopConfig:
    """Configuration for the loop orchestrator."""

    max_iterations: int = 5
    replay_enabled: bool = True
    promotion_mode: str = "auto"
    extract_template: str | None = None
    gate_mode: str = "strict"
    llm: Any = None
    historical_trajectories: tuple[Trajectory, ...] = field(default_factory=tuple)
    existing_rules: tuple[StandingRule, ...] = field(default_factory=tuple)
    rules_dir: str | None = None
    safety_corpus_name: str | None = None
    cutoffs: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


def run_loop(trajectory: Trajectory, config: LoopConfig) -> str | None:
    """Execute the full CauterRule loop for *trajectory*.

    Stages:

    1. **Capture** — record failure context from trajectory.
    2. **Redact** — strip sensitive content.
    3. **Cluster** — group with similar past failures.
    4. **Extract** — LLM extracts candidate rule(s).
    5. **Lint** — validate candidate for quality issues.
    6. **Replay** — test candidate against historical data.
    7. **Tournament** — compare candidates head-to-head.
    8. **Conflict** — detect contradictions with existing rules.
    9. **Promote** — run the real promotion gate (linter, replay evidence,
       safety corpus, confidence cutoff, and #727 source-trust) and persist the
       winning rule via :func:`execute_promotion`.
    10. **Inject** — prepare injection context.

    Args:
        trajectory: The execution trajectory to learn from.
        config: Loop configuration.

    Returns:
        The ID of the persisted rule, or ``None`` if nothing was promoted. A
        rule is only promoted when a destination (``config.rules_dir``) is
        configured and every promotion gate passes; this function never
        fabricates an ID.
    """
    if config.llm is None:
        return None

    # 1. Capture — trajectory itself is the capture.
    # 2. Redact
    redacted = redact_trajectory(trajectory) if not trajectory.redacted else trajectory

    # 3. Cluster — group with historical failures
    # Use single-pass extraction per trajectory for now; clustering integration pending.

    # 4. Extract (pre-extraction gate runs inside multipass_extract)
    candidates: list[CandidateRule] = multipass_extract(
        redacted, config.llm, template=config.extract_template, gate_mode=config.gate_mode
    )
    if not candidates:
        return None

    # 5. Lint — keep each candidate with its linter result for the gate.
    linted: list[tuple[CandidateRule, LinterResult]] = []
    for c in candidates:
        result = lint_rule(
            c.when.trigger,
            c.do.directive,
            existing_rules=list(config.existing_rules) if config.existing_rules else None,
            context=c.when.context,
        )
        if result.passed:
            linted.append((c, result))
    if not linted:
        return None

    # 6-7. Replay + Tournament
    evidence: EvidenceReport
    if config.replay_enabled and config.historical_trajectories:
        ranked = run_tournament([c for c, _ in linted], list(config.historical_trajectories))
        if not ranked:
            return None
        winner = ranked[0]
        winner_candidate = winner.candidate
        evidence = winner.evidence
    else:
        winner_candidate = linted[0][0]
        evidence = build_evidence_report(winner_candidate, list(config.historical_trajectories))

    # 8. Conflict — re-check winner against existing rules
    winner_lint = lint_rule(
        winner_candidate.when.trigger,
        winner_candidate.do.directive,
        existing_rules=list(config.existing_rules) if config.existing_rules else None,
        context=winner_candidate.when.context,
    )
    if not winner_lint.passed:
        return None

    # 9. Promote — real gate (linter + replay evidence + safety + cutoff +
    # #727 source-trust). Without a destination there is nothing to promote to,
    # so return None rather than fabricating an ID that exists in no store.
    if config.rules_dir is None:
        return None
    decision = auto_promote(
        winner_candidate,
        evidence,
        winner_lint,
        corpus_name=config.safety_corpus_name,
        cutoffs=config.cutoffs,
        source_tainted=is_source_tainted(trajectory),
    )
    if decision.verdict != "promote":
        return None
    return execute_promotion(
        winner_candidate,
        {
            "rules_dir": config.rules_dir,
            "source_trajectory": trajectory.id,
            "extracted_by": str(config.extra.get("extracted_by", "loop-orchestrator")),
            "extract_timestamp": trajectory.timestamp,
            "extraction_pass": winner_candidate.extraction_pass,
            "promotion_mode": config.promotion_mode,
            "status": "active",
        },
    )
