"""CandidateRule data model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cauterule.models.rule import RuleDo, RuleWhen


def _require_nonblank(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


@dataclass(frozen=True)
class CandidateRule:
    """A candidate rule produced by extraction before promotion."""

    when: RuleWhen
    do: RuleDo
    confidence: float
    reasoning: str | None = None
    extraction_pass: int = 1
    template: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if self.extraction_pass < 1:
            raise ValueError(f"extraction_pass must be >=1, got {self.extraction_pass}")
        if self.reasoning is not None and not self.reasoning.strip():
            raise ValueError("reasoning must be non-blank if provided")
        if self.template is not None:
            _require_nonblank(self.template, "template")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {
            "when": self.when.to_dict(),
            "do": self.do.to_dict(),
            "confidence": self.confidence,
            "extraction_pass": self.extraction_pass,
        }
        if self.reasoning is not None:
            d["reasoning"] = self.reasoning
        if self.template is not None:
            d["template"] = self.template
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateRule:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(
            when=RuleWhen.from_dict(data.get("when", {})),
            do=RuleDo.from_dict(data.get("do", {})),
            confidence=float(data.get("confidence", 0.0)),
            reasoning=data.get("reasoning"),
            extraction_pass=int(data.get("extraction_pass", 1)),
            template=data.get("template"),
        )
