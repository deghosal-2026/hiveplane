"""Rollback support — git revert any promotion."""

from __future__ import annotations

import re
from pathlib import Path

from cauterule.log import get_logger

_log = get_logger(__name__)

# Full/short SHAs only — anything else (options, paths, compound shell
# fragments) is rejected before any subprocess runs (#505).
_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


def rollback_promotion(commit_hash: str, base_dir: str = "rules") -> bool:
    """Revert a promotion commit via ``git revert --no-edit``.

    Args:
        commit_hash: The hash of the commit to revert (7-40 hex chars).
        base_dir: Directory whose git repo to use.

    Returns:
        ``True`` if the revert succeeded, ``False`` otherwise.

    Raises:
        ValueError: If *commit_hash* is not a valid short/full SHA (#505 —
            blocks option injection such as ``--help`` or ``-h``).
    """
    import subprocess

    if not isinstance(commit_hash, str) or not _HASH_RE.match(commit_hash):
        msg = f"invalid commit hash {commit_hash!r}"
        raise ValueError(msg)
    repo = Path(base_dir).resolve()
    try:
        subprocess.run(
            ["git", "revert", "--no-edit", "--", commit_hash],
            capture_output=True,
            check=True,
            cwd=repo if repo.is_dir() else repo.parent,
        )
    except subprocess.CalledProcessError:
        _log.warning("rollback of %s failed", commit_hash)
        return False
    except FileNotFoundError:
        _log.warning("rollback failed — git executable not found")
        return False
    else:
        return True
