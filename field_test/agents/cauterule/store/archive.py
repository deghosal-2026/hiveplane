"""Archive manager — moves retired rules to ``rules/archived/``."""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

from cauterule.store.manager import resolve_inside, validate_rule_id


def _dump_text_to_file_atomic(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as fh:  # noqa: PTH123 — fd is int, not path
            fh.write(text)
        tmp_path.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def _resolve_rule_path(base_dir: Path, rule_id: str) -> Path:
    """Return the rule file path, trying ``.yaml`` then ``.yml`` (#523)."""
    for suffix in (".yaml", ".yml"):
        try:
            return resolve_inside(base_dir, f"{rule_id}{suffix}")
        except ValueError:
            pass
    return resolve_inside(base_dir, f"{rule_id}.yaml")


def archive_rule(rule_id: str, base_dir: str = "rules") -> Path:
    """Move a retired rule file into the ``archived/`` subdirectory.

    The source file is removed after a successful atomic copy.

    Args:
        rule_id: Id of the rule to archive.
        base_dir: Root rule-store directory.

    Returns:
        The destination :class:`Path` of the archived file.

    Raises:
        FileNotFoundError: If the rule file does not exist.
        ValueError: If *rule_id* is unsafe for paths (#499).
    """
    validate_rule_id(rule_id)
    base = Path(base_dir)
    src = _resolve_rule_path(base, rule_id)
    if not src.is_file():
        msg = f"Rule file not found: {src}"
        raise FileNotFoundError(msg)

    archive_dir = base / "archived"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / src.name

    yaml_text = src.read_text(encoding="utf-8")
    _dump_text_to_file_atomic(yaml_text, dst)
    src.unlink()

    try:
        from cauterule.store.index import IndexManager

        IndexManager(base_dir).remove_entry(rule_id)
    except Exception:
        pass
    try:
        from cauterule.store.git import git_commit

        git_commit(f"archive rule {rule_id}", base_dir)
    except Exception:
        pass

    return dst
