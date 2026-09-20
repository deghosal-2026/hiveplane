"""PromotionDecision data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from cauterule.models._coercion import require_str_tuple

Verdict = Literal["promote", "reject", "needs_review"]
_VALID_VERDICTS: frozenset[str] = frozenset({"promote", "reject", "needs_review"})


@dataclass(frozen=True)
class PromotionDecision:
    """Decision from the promotion gate."""

    verdict: Verdict
    evidence_summary: str | None = None
    approver: str | None = None
    linter_warnings: tuple[str, ...] = field(default_factory=tuple)
    conflicts: tuple[str, ...] = field(default_factory=tuple)
    safety_warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.verdict not in _VALID_VERDICTS:
            raise ValueError(f"verdict must be one of {_VALID_VERDICTS}, got {self.verdict}")
        if self.evidence_summary is not None and not self.evidence_summary.strip():
            raise ValueError("evidence_summary must be non-blank if provided")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {"verdict": self.verdict}
        if self.evidence_summary is not None:
            d["evidence_summary"] = self.evidence_summary
        if self.approver is not None:
            d["approver"] = self.approver
        if self.linter_warnings:
            d["linter_warnings"] = list(self.linter_warnings)
        if self.conflicts:
            d["conflicts"] = list(self.conflicts)
        if self.safety_warnings:
            d["safety_warnings"] = list(self.safety_warnings)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromotionDecision:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(
            verdict=data.get("verdict", "needs_review"),
            evidence_summary=data.get("evidence_summary"),
            approver=data.get("approver"),
            linter_warnings=require_str_tuple(data.get("linter_warnings", []), "linter_warnings"),
            conflicts=require_str_tuple(data.get("conflicts", []), "conflicts"),
            safety_warnings=require_str_tuple(data.get("safety_warnings", []), "safety_warnings"),
        )
