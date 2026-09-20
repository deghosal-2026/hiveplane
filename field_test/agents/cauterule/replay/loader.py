"""Trajectory loader for replay."""

from __future__ import annotations

from pathlib import Path

from cauterule.models.trajectory import Trajectory
from cauterule.serialization.trajectory_jsonl import load_trajectories


def load_from_file(path: Path | str) -> list[Trajectory]:
    """Load trajectories from a single JSONL file."""
    p = Path(path)
    if not p.is_file():
        return []
    return list(load_trajectories(p))


def load_from_dir(base_dir: Path | str) -> list[Trajectory]:
    """Recursively load all ``*.jsonl`` trajectories under *base_dir*."""
    base = Path(base_dir)
    if not base.is_dir():
        return []
    trajectories: list[Trajectory] = []
    for p in base.rglob("*.jsonl"):
        trajectories.extend(load_from_file(p))
    # Deterministic order
    trajectories.sort(key=lambda t: t.id)
    return trajectories


def load_corpus(paths: list[Path | str]) -> list[Trajectory]:
    """Load trajectories from a list of files or directories."""
    result: list[Trajectory] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            result.extend(load_from_dir(path))
        elif path.is_file():
            result.extend(load_from_file(path))
    result.sort(key=lambda t: t.id)
    return result
