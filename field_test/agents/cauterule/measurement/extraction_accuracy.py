"""Extraction-accuracy measurement vs corpus ``expected_rule`` (#730).

Replay measures whether the *matcher* validates a rule. This module measures
whether the *extractor* produced the right rule in the first place, by scoring
the best extracted candidate against the trajectory's ground-truth
``expected_rule``.

Two comparators are reported because they fail differently:

* **token F1** — literal word overlap. Under-counts rewording (a near-verbatim
  rule with different phrasing can score ~0.5), so it is a lower bound.
* **semantic F1** — the replay matcher's :func:`~cauterule.replay.matcher.match_score`
  (token + bigram, plus embeddings when enabled). This is the headline
  ``extraction_f1``; a reworded-but-correct rule scores high.
* **directive F1** — word overlap on the ``do`` half.

``extraction_agreement`` is the rate of trajectories whose extracted *trigger*
is semantically equivalent to the ``expected_rule`` trigger. The directive is
reported as ``directive_f1`` alongside but does **not** gate agreement: short
directive phrases are unreliable under both literal token F1 and embeddings
(J6), so gating the headline on them only added pessimism. Trajectories without
an ``expected_rule`` are excluded (``n/a``), never counted as 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "it",
        "is",
        "be",
        "then",
        "when",
        "before",
        "after",
        "if",
    }
)


def _tokens(text: str) -> set[str]:
    return {
        t for t in _TOKEN_RE.split(text.lower()) if t and t not in _STOPWORDS and len(t) > 1
    }


def _f1(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    if overlap == 0:
        return 0.0
    precision = overlap / len(a)
    recall = overlap / len(b)
    return 2 * precision * recall / (precision + recall)


def parse_expected_rule(expected_rule: str) -> tuple[str, str]:
    """Split an ``expected_rule`` into (trigger, directive).

    The corpus format is ``"when <trigger>, <directive>"``; a missing comma
    yields the whole string as the trigger with an empty directive.
    """
    text = expected_rule.strip()
    if text.lower().startswith("when "):
        text = text[5:]
    if "," in text:
        trigger, directive = text.split(",", 1)
        return trigger.strip(), directive.strip()
    return text, ""


def _semantic_similarity(a: str, b: str) -> float:
    """Symmetric matcher score between two short rule phrases."""
    from cauterule.models.candidate import CandidateRule
    from cauterule.models.rule import RuleDo, RuleWhen
    from cauterule.models.trajectory import Trajectory
    from cauterule.replay.matcher import match_score

    if not a.strip() or not b.strip():
        return 0.0

    def _one(trigger: str, text: str) -> float:
        cand = CandidateRule(
            when=RuleWhen(trigger=trigger), do=RuleDo(directive="x"), confidence=1.0
        )
        traj = Trajectory(id="x", timestamp="t", task=text, steps=(), success=False)
        return match_score(cand, traj)

    return max(_one(a, b), _one(b, a))


@dataclass(frozen=True)
class ExtractionScore:
    """Comparison of one extracted rule against its ``expected_rule``."""

    token_f1: float
    semantic_f1: float
    directive_f1: float
    token_agreement: bool
    agreement: bool


def score_rule(
    trigger: str,
    directive: str,
    expected_rule: str,
    *,
    match_threshold: float = 0.6,
) -> ExtractionScore:
    """Score extracted (*trigger*, *directive*) against *expected_rule*.

    ``agreement`` (the headline) is decided on the *trigger* only, using the
    semantic comparator — the same comparator the replay matcher uses. The
    directive is scored as ``directive_f1`` and reported alongside but does not
    gate agreement, because short directive phrases are unreliable under both
    literal token F1 and embeddings (J6). ``token_agreement`` is the strict
    literal-match signal on the trigger, reported separately.
    """
    exp_trigger, exp_directive = parse_expected_rule(expected_rule)
    token_f1 = _f1(_tokens(trigger), _tokens(exp_trigger))
    semantic_f1 = _semantic_similarity(trigger, exp_trigger)
    directive_f1 = _f1(_tokens(directive), _tokens(exp_directive)) if exp_directive else 0.0
    return ExtractionScore(
        token_f1=token_f1,
        semantic_f1=semantic_f1,
        directive_f1=directive_f1,
        token_agreement=token_f1 >= match_threshold,
        agreement=semantic_f1 >= match_threshold,
    )


@dataclass(frozen=True)
class ExtractionAccuracyReport:
    """Aggregate extraction accuracy over trajectories carrying ``expected_rule``."""

    n: int = 0
    token_f1: float = 0.0
    semantic_f1: float = 0.0
    directive_f1: float = 0.0
    token_agreement: float = 0.0
    agreement: float = 0.0

    def to_dict(self) -> dict[str, float | int | None]:
        """Return a JSON-serializable dict.

        When ``n == 0`` there is no ground truth to measure against, so the
        rates serialize as ``None`` (n/a) rather than a hard ``0.0`` that
        would be indistinguishable from a total failure (J10).
        """
        if self.n == 0:
            return {
                "n": 0,
                "token_f1": None,
                "semantic_f1": None,
                "directive_f1": None,
                "token_agreement": None,
                "agreement": None,
            }
        return {
            "n": self.n,
            "token_f1": round(self.token_f1, 4),
            "semantic_f1": round(self.semantic_f1, 4),
            "directive_f1": round(self.directive_f1, 4),
            "token_agreement": round(self.token_agreement, 4),
            "agreement": round(self.agreement, 4),
        }


@dataclass(frozen=True)
class ExtractionRecord:
    """One extracted rule paired with its expected rule."""

    trigger: str
    directive: str
    expected_rule: str = field(default="")


def records_from_results(results: list[dict[str, object]]) -> list[ExtractionRecord]:
    """Map runner ``done`` records to extraction records.

    Uses the best candidate (``result["best"]["candidate"]``) and
    ``result["trajectory"]["expected_rule"]``; records missing either are
    skipped so they never dilute the metric.
    """
    records: list[ExtractionRecord] = []
    for result in results:
        traj = result.get("trajectory")
        expected = traj.get("expected_rule") if isinstance(traj, dict) else None
        best = result.get("best")
        candidate = best.get("candidate") if isinstance(best, dict) else None
        if not expected or not isinstance(candidate, dict):
            continue
        records.append(
            ExtractionRecord(
                trigger=str(candidate.get("when", "")),
                directive=str(candidate.get("do", "")),
                expected_rule=str(expected),
            )
        )
    return records


def measure_extraction_accuracy(
    records: list[ExtractionRecord],
    *,
    match_threshold: float = 0.6,
) -> ExtractionAccuracyReport:
    """Aggregate extraction scores over *records*."""
    if not records:
        return ExtractionAccuracyReport()
    scores = [
        score_rule(
            r.trigger,
            r.directive,
            r.expected_rule,
            match_threshold=match_threshold,
        )
        for r in records
    ]
    n = len(scores)
    return ExtractionAccuracyReport(
        n=n,
        token_f1=sum(s.token_f1 for s in scores) / n,
        semantic_f1=sum(s.semantic_f1 for s in scores) / n,
        directive_f1=sum(s.directive_f1 for s in scores) / n,
        token_agreement=sum(1 for s in scores if s.token_agreement) / n,
        agreement=sum(1 for s in scores if s.agreement) / n,
    )
