"""StandingRule data model.

Defines the structured *when X, do Y* rule with provenance and lifecycle
fields per ``docs/design/standing-rule-format-design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from cauterule.models._coercion import require_str_tuple

Status = Literal["active", "retired", "superseded"]
_VALID_STATUSES: frozenset[str] = frozenset({"active", "retired", "superseded"})


def _require_nonblank(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


def _require_confidence(value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0], got {value}")


@dataclass(frozen=True)
class RuleWhen:
    """Condition that triggers a rule."""

    trigger: str
    context: tuple[str, ...] = field(default_factory=tuple)
    signature: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.trigger, "when.trigger")
        for c in self.context:
            _require_nonblank(c, "when.context item")
        if self.signature is not None:
            _require_nonblank(self.signature, "when.signature")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {"trigger": self.trigger}
        if self.context:
            d["context"] = list(self.context)
        if self.signature is not None:
            d["signature"] = self.signature
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleWhen:
        """Create from a dict produced by :meth:`to_dict`."""
        trigger = data.get("trigger", "")
        context = require_str_tuple(data.get("context", []), "when.context")
        signature = data.get("signature")
        return cls(trigger=trigger, context=context, signature=signature)


@dataclass(frozen=True)
class RuleDo:
    """Directive to follow when the rule fires."""

    directive: str
    because: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.directive, "do.directive")
        if self.because is not None and not self.because.strip():
            raise ValueError("do.because must be non-blank if provided")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {"directive": self.directive}
        if self.because is not None:
            d["because"] = self.because
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleDo:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(directive=data.get("directive", ""), because=data.get("because"))


@dataclass(frozen=True)
class ReplayEvidence:
    """Evidence from historical replay testing."""

    failures_prevented: tuple[str, ...] = field(default_factory=tuple)
    successes_broken: tuple[str, ...] = field(default_factory=tuple)
    precision: float = 0.0
    recall: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.precision <= 1.0:
            raise ValueError(f"precision must be in [0.0, 1.0], got {self.precision}")
        if not 0.0 <= self.recall <= 1.0:
            raise ValueError(f"recall must be in [0.0, 1.0], got {self.recall}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "failures_prevented": list(self.failures_prevented),
            "successes_broken": list(self.successes_broken),
            "precision": self.precision,
            "recall": self.recall,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReplayEvidence:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(
            failures_prevented=tuple(data.get("failures_prevented", [])),
            successes_broken=tuple(data.get("successes_broken", [])),
            precision=float(data.get("precision", 0.0)),
            recall=float(data.get("recall", 0.0)),
        )


@dataclass(frozen=True)
class Provenance:
    """Full provenance chain for a promoted rule."""

    source_trajectory: str
    extracted_by: str
    extract_timestamp: str
    extraction_pass: int
    draft_tournament_rank: int | None = None
    replay_evidence: ReplayEvidence | None = None
    promotion_commit: str | None = None
    promotion_mode: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.source_trajectory, "provenance.source_trajectory")
        _require_nonblank(self.extracted_by, "provenance.extracted_by")
        _require_nonblank(self.extract_timestamp, "provenance.extract_timestamp")
        if self.extraction_pass < 1:
            raise ValueError(f"extraction_pass must be >=1, got {self.extraction_pass}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {
            "source_trajectory": self.source_trajectory,
            "extracted_by": self.extracted_by,
            "extract_timestamp": self.extract_timestamp,
            "extraction_pass": self.extraction_pass,
        }
        if self.draft_tournament_rank is not None:
            d["draft_tournament_rank"] = self.draft_tournament_rank
        if self.replay_evidence is not None:
            d["replay_evidence"] = self.replay_evidence.to_dict()
        if self.promotion_commit is not None:
            d["promotion_commit"] = self.promotion_commit
        if self.promotion_mode is not None:
            d["promotion_mode"] = self.promotion_mode
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        """Create from a dict produced by :meth:`to_dict`."""
        ev = data.get("replay_evidence")
        return cls(
            source_trajectory=data.get("source_trajectory", ""),
            extracted_by=data.get("extracted_by", ""),
            extract_timestamp=data.get("extract_timestamp", ""),
            extraction_pass=int(data.get("extraction_pass", 1)),
            draft_tournament_rank=data.get("draft_tournament_rank"),
            replay_evidence=ReplayEvidence.from_dict(ev) if isinstance(ev, dict) else None,
            promotion_commit=data.get("promotion_commit"),
            promotion_mode=data.get("promotion_mode"),
        )


@dataclass(frozen=True)
class StandingRule:
    """A promoted standing rule with full lifecycle metadata."""

    id: str
    when: RuleWhen
    do: RuleDo
    confidence: float
    provenance: Provenance
    status: Status
    promoted_at: str
    hit_count: int = 0
    last_match: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    taxonomy: str | None = None
    template: str | None = None
    pack: str | None = None
    retired_at: str | None = None
    retirement_reason: str | None = None
    superseded_by: str | None = None
    prevented_count: int = 0
    broke_count: int = 0
    neutral_count: int = 0
    last_outcome: str | None = None
    last_outcome_at: str | None = None
    outcome_trend: tuple[int, ...] = field(default_factory=tuple)
    specificity: float | None = None
    specificity_inputs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.id, "id")
        _require_confidence(self.confidence)
        _require_nonblank(self.promoted_at, "promoted_at")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}, got {self.status}")
        if self.hit_count < 0:
            raise ValueError(f"hit_count must be >=0, got {self.hit_count}")
        if self.prevented_count < 0:
            raise ValueError(f"prevented_count must be >=0, got {self.prevented_count}")
        if self.broke_count < 0:
            raise ValueError(f"broke_count must be >=0, got {self.broke_count}")
        if self.neutral_count < 0:
            raise ValueError(f"neutral_count must be >=0, got {self.neutral_count}")
        if self.last_outcome is not None and self.last_outcome not in (
            "prevented",
            "broke",
            "neutral",
        ):
            raise ValueError(f"invalid last_outcome {self.last_outcome!r}")
        if self.outcome_trend:
            for v in self.outcome_trend:
                if v not in (1, 0, -1):
                    raise ValueError(f"outcome_trend items must be in (-1, 0, 1), got {v}")
        if self.specificity is not None and not 0.0 <= self.specificity <= 1.0:
            raise ValueError(f"specificity must be in [0.0, 1.0], got {self.specificity}")
        for t in self.tags:
            _require_nonblank(t, "tags item")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict for YAML/JSON persistence."""
        d: dict[str, Any] = {
            "id": self.id,
            "when": self.when.to_dict(),
            "do": self.do.to_dict(),
            "confidence": self.confidence,
            "provenance": self.provenance.to_dict(),
            "status": self.status,
            "promoted_at": self.promoted_at,
            "hit_count": self.hit_count,
        }
        if self.last_match is not None:
            d["last_match"] = self.last_match
        if self.tags:
            d["tags"] = list(self.tags)
        if self.taxonomy is not None:
            d["taxonomy"] = self.taxonomy
        if self.template is not None:
            d["template"] = self.template
        if self.pack is not None:
            d["pack"] = self.pack
        if self.retired_at is not None:
            d["retired_at"] = self.retired_at
        if self.retirement_reason is not None:
            d["retirement_reason"] = self.retirement_reason
        if self.superseded_by is not None:
            d["superseded_by"] = self.superseded_by
        if self.prevented_count:
            d["prevented_count"] = self.prevented_count
        if self.broke_count:
            d["broke_count"] = self.broke_count
        if self.neutral_count:
            d["neutral_count"] = self.neutral_count
        if self.last_outcome is not None:
            d["last_outcome"] = self.last_outcome
        if self.last_outcome_at is not None:
            d["last_outcome_at"] = self.last_outcome_at
        if self.outcome_trend:
            d["outcome_trend"] = list(self.outcome_trend)
        if self.specificity is not None:
            d["specificity"] = self.specificity
        if self.specificity_inputs:
            d["specificity_inputs"] = dict(self.specificity_inputs)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StandingRule:
        """Create from a dict produced by :meth:`to_dict`.

        Raises:
            ValueError: If the required ``status`` field is absent (#593 —
                a missing status must not silently resurrect a retired rule
                as ``active``).
        """
        if "status" not in data or data.get("status") is None:
            raise ValueError("StandingRule missing required 'status' field")
        return cls(
            id=data.get("id", ""),
            when=RuleWhen.from_dict(data.get("when", {})),
            do=RuleDo.from_dict(data.get("do", {})),
            confidence=float(data.get("confidence", 0.0)),
            provenance=Provenance.from_dict(data.get("provenance", {})),
            status=data["status"],
            promoted_at=data.get("promoted_at", ""),
            hit_count=int(data.get("hit_count", 0)),
            last_match=data.get("last_match"),
            tags=require_str_tuple(data.get("tags", []), "tags"),
            taxonomy=data.get("taxonomy"),
            template=data.get("template"),
            pack=data.get("pack"),
            retired_at=data.get("retired_at"),
            retirement_reason=data.get("retirement_reason"),
            superseded_by=data.get("superseded_by"),
            prevented_count=int(data.get("prevented_count", 0)),
            broke_count=int(data.get("broke_count", 0)),
            neutral_count=int(data.get("neutral_count", 0)),
            last_outcome=data.get("last_outcome"),
            last_outcome_at=data.get("last_outcome_at"),
            outcome_trend=tuple(int(v) for v in data.get("outcome_trend", [])),
            specificity=(
                float(data["specificity"]) if data.get("specificity") is not None else None
            ),
            specificity_inputs=dict(data.get("specificity_inputs", {})),
        )
