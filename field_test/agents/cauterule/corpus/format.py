"""Corpus format specification — metadata schema and constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Tier = Literal["tiny", "small", "medium", "large"]

CORPUS_SCHEMA_VERSION: str = "1.0"

CORPUS_METADATA_FIELDS: list[str] = [
    "id",
    "tier",
    "domain",
    "quality_label",
    "trajectory_count",
    "gold_rule_ids",
]


@dataclass(frozen=True)
class CorpusMetadata:
    """Metadata describing a corpus collection."""

    id: str
    tier: Tier
    domain: str | None = None
    quality_label: str | None = None
    trajectory_count: int = 0
    gold_rule_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        valid_tiers: frozenset[str] = frozenset({"tiny", "small", "medium", "large"})
        if self.tier not in valid_tiers:
            raise ValueError(f"tier must be one of {valid_tiers}, got {self.tier!r}")
        if not self.id or not self.id.strip():
            raise ValueError("id must be a non-blank string")
        if self.trajectory_count < 0:
            raise ValueError(f"trajectory_count must be >=0, got {self.trajectory_count}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {
            "id": self.id,
            "tier": self.tier,
            "trajectory_count": self.trajectory_count,
        }
        if self.domain is not None:
            d["domain"] = self.domain
        if self.quality_label is not None:
            d["quality_label"] = self.quality_label
        if self.gold_rule_ids:
            d["gold_rule_ids"] = list(self.gold_rule_ids)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorpusMetadata:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(
            id=data.get("id", ""),
            tier=data.get("tier", "tiny"),
            domain=data.get("domain"),
            quality_label=data.get("quality_label"),
            trajectory_count=int(data.get("trajectory_count", 0)),
            gold_rule_ids=tuple(data.get("gold_rule_ids", [])),
        )
