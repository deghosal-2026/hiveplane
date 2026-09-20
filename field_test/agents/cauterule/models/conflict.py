"""ConflictReport data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from cauterule.models._coercion import require_str_tuple

ConflictType = Literal["contradiction", "duplicate", "overlap", "specificity"]
_VALID_TYPES: frozenset[str] = frozenset({"contradiction", "duplicate", "overlap", "specificity"})


@dataclass(frozen=True)
class ConflictReport:
    """Report of conflicts between rules."""

    type: ConflictType
    rules: tuple[str, ...]
    trigger: str | None = None
    resolution: str | None = None
    specificity_scores: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in _VALID_TYPES:
            raise ValueError(f"type must be one of {_VALID_TYPES}, got {self.type}")
        if not self.rules:
            raise ValueError("rules must be non-empty")
        for r in self.rules:
            if not r or not r.strip():
                raise ValueError("rules items must be non-blank")
        if self.trigger is not None and not self.trigger.strip():
            raise ValueError("trigger must be non-blank if provided")
        for k, v in self.specificity_scores.items():
            if not k.strip():
                raise ValueError("specificity_scores keys must be non-blank")
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"specificity_scores values must be in [0,1], got {v}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {"type": self.type, "rules": list(self.rules)}
        if self.trigger is not None:
            d["trigger"] = self.trigger
        if self.resolution is not None:
            d["resolution"] = self.resolution
        if self.specificity_scores:
            d["specificity_scores"] = dict(self.specificity_scores)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConflictReport:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(
            type=data.get("type", "overlap"),
            rules=require_str_tuple(data.get("rules", []), "rules"),
            trigger=data.get("trigger"),
            resolution=data.get("resolution"),
            specificity_scores=dict(data.get("specificity_scores", {})),
        )
