"""YAML serialization for StandingRule.

Provides round-trip ``StandingRule`` ↔ YAML via ``to_dict``/``from_dict``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

from cauterule.models.rule import StandingRule

_logger = logging.getLogger(__name__)

# Records from the most recent :func:`load_rules_from_dir` call that
# failed to parse/validate, as ``(relative_path, error)`` tuples.
last_load_errors: list[tuple[str, str]] = []


def dump_rule(rule: StandingRule) -> str:
    """Serialize *rule* to a YAML string."""
    return yaml.safe_dump(rule.to_dict(), sort_keys=False, allow_unicode=True)


def load_rule(yaml_str: str) -> StandingRule:
    """Deserialize a YAML string to a :class:`StandingRule`."""
    data = yaml.safe_load(yaml_str)
    if not isinstance(data, dict):
        raise ValueError("YAML must decode to a mapping")
    return StandingRule.from_dict(data)


def dump_rule_to_file(rule: StandingRule, path: str | Path) -> None:
    """Write *rule* to *path* as YAML.

    Creates parent directories as needed.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_rule(rule), encoding="utf-8")


def dump_rule_to_file_atomic(rule: StandingRule, path: str | Path) -> None:
    """Write *rule* to *path* via an atomic ``tmp → os.replace`` (#523).

    The destination file is left intact on write failure (truncation-safe).
    """
    import tempfile

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dump_rule(rule))
        tmp_path.replace(p)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def load_rule_from_file(path: str | Path) -> StandingRule:
    """Load a :class:`StandingRule` from a YAML file at *path*."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return load_rule(text)


def _quarantine_file(path: Path, directory: Path, exc: Exception) -> None:
    """Move a bad rule file to ``<directory>/.quarantine/`` and record it.

    Args:
        path: The rule file that failed to parse/validate.
        directory: The store root. The quarantine dir is created under it.
        exc: The exception that caused the quarantine.
    """
    quarantine_dir = directory / ".quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    dst = quarantine_dir / path.name
    if dst.exists():
        # Avoid clobbering an earlier quarantined file of the same name
        # (e.g. two packs containing a malformed <same-id>.yaml).
        unique = quarantine_dir / f"{path.stem}.{int(time.time() * 1000)}{path.suffix}"
        dst = unique
    try:
        shutil.move(str(path), str(dst))
    except OSError:
        dst = path  # leave in place; still record the failure
    record = {
        "path": str(dst),
        "error": f"{type(exc).__name__}: {exc}",
        "moved_at": datetime.now(UTC).isoformat(),
    }
    record_path = quarantine_dir / "index.jsonl"
    with record_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_rules_from_dir(
    directory: str | Path,
    *,
    strict: bool = False,
    quarantine: bool = True,
) -> list[StandingRule]:
    """Load all ``*.yaml``/``*.yml`` rules from *directory* recursively.

    Walks the whole tree (pack subdirs included) and skips any non-rule
    file whose stem is ``index`` or ``manifest`` (pack manifests) at any
    depth. A single malformed YAML file no longer aborts the load: by
    default the offending file is moved to ``<directory>/.quarantine/``
    (recorded in ``index.jsonl``) and a warning is logged, while valid
    rules before and after it are still returned.

    Args:
        directory: Root directory to search recursively for rule files.
        strict: When ``True``, re-raise on the first failed file instead of
            quarantining and continuing.
        quarantine: When ``False``, leave failing files in place (error is
            still logged and surfaced via :data:`last_load_errors`).

    Returns:
        All rules that parsed and validated successfully.

    Raises:
        ValueError: If *strict* is ``True`` and a file fails to load.
    """
    last_load_errors.clear()
    d = Path(directory)
    if not d.is_dir():
        return []

    # Skip quarantine/archive subtrees so re-loads are idempotent (#code-review):
    # quarantined files would otherwise be re-failed and archived rules would
    # resurface as active.
    _skipped_subdirs = frozenset({".quarantine", "archived"})
    files = sorted(
        p
        for p in (set(d.rglob("*.yaml")) | set(d.rglob("*.yml")))
        if not _skipped_subdirs.intersection(p.relative_to(d).parts[:-1])
    )
    rules: list[StandingRule] = []
    # Pack manifests, the dep lockfile, and store indexes are not rules and
    # must never be quarantined (#554: the loader ate pack.yaml).
    _non_rule_files = frozenset(
        {
            "index.yaml",
            "index.yml",
            "manifest.yaml",
            "manifest.yml",
            "pack.yaml",
            "pack.yml",
            "packs.lock.yaml",
            "packs.lock.yml",
        }
    )
    for p in files:
        if p.stem in {"index", "manifest"} or p.name in _non_rule_files:
            continue
        if not p.is_file():
            continue
        try:
            rules.append(load_rule_from_file(p))
        except Exception as exc:
            last_load_errors.append((str(p), f"{type(exc).__name__}: {exc}"))
            _logger.warning(
                "skipping unloadable rule file %s: %s",
                p,
                exc,
                extra={"rule_file": str(p)},
            )
            if strict:
                msg = f"failed to load {p}: {type(exc).__name__}: {exc}"
                raise ValueError(msg) from exc
            if quarantine:
                _quarantine_file(p, d, exc)
    return rules
