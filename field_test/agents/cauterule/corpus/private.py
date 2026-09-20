"""Private local corpus — wrap a local trace directory as a corpus."""

from __future__ import annotations

from pathlib import Path

from cauterule.models.trajectory import Trajectory
from cauterule.serialization.trajectory_jsonl import load_trajectories_result


class PrivateCorpus:
    """A corpus backed by a local directory of JSONL trace files.

    Scans *root_dir* for ``*.jsonl`` files and loads trajectories on
    demand.  Supports filtering by domain, quality label, and success
    status.
    """

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir)
        if not self._root.is_dir():
            raise ValueError(f"root_dir must be an existing directory: {root_dir}")
        self._trajectories: list[Trajectory] | None = None

    @property
    def root_dir(self) -> str:
        """Return the root directory path."""
        return str(self._root)

    def _load_all(self) -> list[Trajectory]:
        if self._trajectories is not None:
            return self._trajectories
        trajectories: list[Trajectory] = []
        for fpath in sorted(self._root.glob("*.jsonl")):
            # Resilient load (#597): one bad line skips with a warning
            # instead of aborting the whole private corpus.
            result = load_trajectories_result(fpath)
            trajectories.extend(result.loaded)
        self._trajectories = trajectories
        return trajectories

    def trajectories(
        self,
        domain: str | None = None,
        quality_label: str | None = None,
        success: bool | None = None,
    ) -> list[Trajectory]:
        """Return filtered trajectories from the corpus.

        Args:
            domain: If set, only return trajectories with this domain.
            quality_label: If set, only return trajectories with this quality label.
            success: If set, only return trajectories matching this success value.

        Returns:
            List of matching :class:`Trajectory`.
        """
        all_trajs = self._load_all()
        result = list(all_trajs)
        if domain is not None:
            result = [t for t in result if t.domain == domain]
        if quality_label is not None:
            result = [t for t in result if t.quality_label == quality_label]
        if success is not None:
            result = [t for t in result if t.success == success]
        return result

    def count(self) -> int:
        """Return total number of trajectories."""
        return len(self._load_all())

    def reload(self) -> None:
        """Force re-read from disk on next access."""
        self._trajectories = None
