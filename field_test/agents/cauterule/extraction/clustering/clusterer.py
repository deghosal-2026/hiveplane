"""Failure clusterer."""

from __future__ import annotations

from cauterule.extraction.clustering.config import ClusteringConfig
from cauterule.extraction.clustering.similarity import similarity
from cauterule.models.trajectory import Trajectory


def cluster_trajectories(
    trajectories: list[Trajectory],
    config: ClusteringConfig | None = None,
    threshold: float | None = None,
) -> list[list[Trajectory]]:
    """Group similar failures into clusters.

    One extraction per cluster (not per failure).

    Args:
        trajectories: List of trajectories to cluster (typically failures).
        config: Clustering config (threshold, enabled).
        threshold: Override threshold (0.0-1.0). If None, uses config.threshold.

    Returns:
        List of clusters, each a list of trajectories. Order preserves input order.
        If clustering disabled, returns each trajectory as its own cluster.
    """
    cfg = config or ClusteringConfig()
    if not cfg.enabled:
        return [[t] for t in trajectories]
    th = threshold if threshold is not None else cfg.threshold
    if not 0.0 <= th <= 1.0:
        raise ValueError(f"threshold must be in [0.0, 1.0], got {th}")

    clusters: list[list[Trajectory]] = []
    for traj in trajectories:
        placed = False
        for cluster in clusters:
            # Compare to first member (representative).
            rep = cluster[0]
            if similarity(traj, rep) >= th:
                cluster.append(traj)
                placed = True
                break
        if not placed:
            clusters.append([traj])
    return clusters
