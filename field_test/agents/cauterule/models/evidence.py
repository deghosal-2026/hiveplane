"""EvidenceReport data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Verdict = Literal["pass", "fail", "inconclusive"]
_VALID_VERDICTS: frozenset[str] = frozenset({"pass", "fail", "inconclusive"})

InconclusiveReason = Literal[
    "broad_trigger", "matcher_gap", "corpus_mismatch", "ambiguous_evidence"
]
_VALID_REASONS: frozenset[str] = frozenset(
    {"broad_trigger", "matcher_gap", "corpus_mismatch", "ambiguous_evidence"}
)


@dataclass(frozen=True)
class EvidenceReport:
    """Report from replay-testing a candidate against history."""

    failures_prevented: tuple[str, ...] = field(default_factory=tuple)
    successes_broken: tuple[str, ...] = field(default_factory=tuple)
    near_misses: tuple[str, ...] = field(default_factory=tuple)
    failures_missed: tuple[str, ...] = field(default_factory=tuple)
    precision: float = 0.0
    recall: float = 0.0
    verdict: Verdict = "inconclusive"
    replay_trace: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    inconclusive_reason: InconclusiveReason | None = None
    verdict_reason: str | None = None
    corpus_hash: str | None = None
    outcome_precision: float | None = None
    outcome_verdict: Verdict | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.precision <= 1.0:
            raise ValueError(f"precision must be in [0.0, 1.0], got {self.precision}")
        if not 0.0 <= self.recall <= 1.0:
            raise ValueError(f"recall must be in [0.0, 1.0], got {self.recall}")
        if self.outcome_precision is not None and not 0.0 <= self.outcome_precision <= 1.0:
            raise ValueError(
                f"outcome_precision must be in [0.0, 1.0], got {self.outcome_precision}"
            )
        if self.verdict not in _VALID_VERDICTS:
            raise ValueError(f"verdict must be one of {_VALID_VERDICTS}, got {self.verdict}")
        if self.outcome_verdict is not None and self.outcome_verdict not in _VALID_VERDICTS:
            raise ValueError(
                f"outcome_verdict must be one of {_VALID_VERDICTS}, got {self.outcome_verdict}"
            )
        reason = self.inconclusive_reason
        if reason is not None and reason not in _VALID_REASONS:
            msg = f"inconclusive_reason must be one of {_VALID_REASONS}, got {reason}"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {
            "failures_prevented": list(self.failures_prevented),
            "successes_broken": list(self.successes_broken),
            "near_misses": list(self.near_misses),
            "failures_missed": list(self.failures_missed),
            "precision": self.precision,
            "recall": self.recall,
            "verdict": self.verdict,
            "replay_trace": list(self.replay_trace),
        }
        if self.inconclusive_reason is not None:
            d["inconclusive_reason"] = self.inconclusive_reason
        if self.verdict_reason is not None:
            d["verdict_reason"] = self.verdict_reason
        if self.corpus_hash is not None:
            d["corpus_hash"] = self.corpus_hash
        if self.outcome_precision is not None:
            d["outcome_precision"] = self.outcome_precision
        if self.outcome_verdict is not None:
            d["outcome_verdict"] = self.outcome_verdict
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceReport:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(
            failures_prevented=tuple(data.get("failures_prevented", [])),
            successes_broken=tuple(data.get("successes_broken", [])),
            near_misses=tuple(data.get("near_misses", [])),
            failures_missed=tuple(data.get("failures_missed", [])),
            precision=float(data.get("precision", 0.0)),
            recall=float(data.get("recall", 0.0)),
            verdict=data.get("verdict", "inconclusive"),
            replay_trace=tuple(data.get("replay_trace", [])),
            inconclusive_reason=data.get("inconclusive_reason"),
            verdict_reason=data.get("verdict_reason"),
            corpus_hash=data.get("corpus_hash"),
            outcome_precision=(
                float(data["outcome_precision"])
                if data.get("outcome_precision") is not None
                else None
            ),
            outcome_verdict=data.get("outcome_verdict"),
        )
