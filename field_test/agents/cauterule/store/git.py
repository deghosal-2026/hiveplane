"""Git operations — commit promotion, retirement, and consolidation."""

from __future__ import annotations

from pathlib import Path

from cauterule.log import get_logger

_log = get_logger(__name__)


def git_commit(message: str, base_dir: str = "rules") -> str | None:
    """Stage and commit changes under *base_dir*.

    Args:
        message: Commit message.
        base_dir: Directory whose git repo to use (must be inside a git
                  worktree).

    Returns:
        The commit hash, or ``None`` if the directory is not inside a git
        repository. Callers MUST check for ``None`` — it means no commit
        was created (#505).
    """
    import subprocess

    repo = Path(base_dir).resolve()
    try:
        subprocess.run(
            ["git", "add", "--", str(repo)],
            capture_output=True,
            check=True,
            cwd=repo if repo.is_dir() else repo.parent,
        )
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo if repo.is_dir() else repo.parent,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo if repo.is_dir() else repo.parent,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as exc:
        raw = exc.stderr or exc.output or ""
        detail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        _log.error("git_commit failed (rc=%s): %s", exc.returncode, detail[:500])
        return None
    except FileNotFoundError:
        _log.error("git_commit failed — git executable not found")
        return None


def _extract_hash(output: str) -> str | None:
    """Extract the commit hash from ``git commit`` output."""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("["):
            parts = line.split()
            for part in parts:
                part = part.strip("[];")
                if len(part) == 7 and all(c in "0123456789abcdef" for c in part):
                    return part
                if len(part) == 40 and all(c in "0123456789abcdef" for c in part):
                    return part
    return None
