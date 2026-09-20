"""Public synthetic corpus loader."""

from __future__ import annotations

from pathlib import Path

from cauterule.models.trajectory import Trajectory
from cauterule.serialization.trajectory_jsonl import load_trajectories


def load_public_corpus(path: str | None = None) -> list[Trajectory]:
    """Load the public synthetic corpus from JSONL files.

    Looks for ``*.jsonl`` files under *path* (defaults to ``corpus/public/``
    relative to the project root).

    Args:
        path: Directory containing public corpus JSONL files.

    Returns:
        List of :class:`Trajectory`.
    """
    if path is None:
        # Look relative to this file's project location
        here = (
            Path(__file__).resolve().parent.parent.parent.parent
        )  # src/cauterule/corpus/ -> project root
        path = str(here / "corpus" / "public")

    p = Path(path)
    if not p.is_dir():
        return []

    trajectories: list[Trajectory] = []
    for fpath in sorted(p.glob("*.jsonl")):
        trajectories.extend(load_trajectories(fpath))
    return trajectories
