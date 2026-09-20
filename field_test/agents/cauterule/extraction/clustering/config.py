"""Cluster config."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClusteringConfig:
    """Configuration for failure clustering."""

    enabled: bool = True
    threshold: float = 0.85

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be in [0.0, 1.0], got {self.threshold}")
