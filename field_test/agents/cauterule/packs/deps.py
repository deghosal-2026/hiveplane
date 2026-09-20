"""Dependency resolution for rule packs (#548).

Backtracking solver over ``deps`` constraints with cycle detection,
lockfile (``packs.lock.yaml``) read/write, and actionable conflict errors.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from cauterule.packs import semver

LOCKFILE = "packs.lock.yaml"
_DEP_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*(.*)$")


def parse_dep(entry: str) -> tuple[str, str]:
    """Split a dep entry (``pack-docker >=1.0.0,<2.0.0``) into (name, constraint)."""
    match = _DEP_RE.match(entry.strip())
    if not match or not match.group(1):
        raise ValueError(f"invalid dep entry {entry!r}: want '<name> [<constraint>]'")
    return match.group(1), match.group(2).strip()


def resolve(
    requested: dict[str, str],
    available: dict[str, dict[str, Any]],
    installed: dict[str, str] | None = None,
    upgrade: bool = False,
) -> dict[str, str]:
    """Resolve pinned versions for *requested* packs.

    Args:
        requested: Mapping of pack name → constraint ("" means any release).
        available: Mapping of pack name → {"versions": [...], "deps": {version: [dep entries]}}.
        installed: Already-installed pins (kept unless *upgrade*).
        upgrade: When True, ignore installed pins and take highest compatible.

    Returns:
        Mapping of pack name → resolved version.

    Raises:
        ValueError: On cycles, unknown packs, or unsatisfiable constraints,
            with an actionable message naming the requirers.
    """
    installed = installed or {}
    resolved: dict[str, str] = {}
    requirers: dict[str, list[str]] = {}
    stack: list[str] = []

    def _note(pack: str, requirer: str) -> None:
        requirers.setdefault(pack, [])
        if requirer not in requirers[pack]:
            requirers[pack].append(requirer)

    def _candidates(pack: str, constraint: str) -> list[str]:
        info = available.get(pack)
        if info is None:
            raise ValueError(f"unknown pack {pack!r} (no releases found)")
        raw_versions = info.get("versions", [])
        versions = [v for v in raw_versions if isinstance(v, str) and _ok(v, constraint)]
        if not upgrade and installed.get(pack) and _ok(installed[pack], constraint):
            pinned = installed[pack]
            versions = [*versions, pinned]
            versions = sorted(set(versions), key=semver.parse, reverse=True)
            versions.remove(pinned)
            versions.insert(0, pinned)
        else:
            versions = sorted(versions, key=semver.parse, reverse=True)
        return versions

    def _ok(version: str, constraint: str) -> bool:
        if not constraint:
            parsed = semver.parse(version)
            return not parsed.is_prerelease
        try:
            return semver.satisfies(version, constraint)
        except ValueError:
            return False

    def _visit(pack: str, constraint: str, requirer: str) -> None:
        _note(pack, requirer)
        if pack in stack:
            cycle = " → ".join([*stack, pack])
            raise ValueError(f"dependency cycle detected: {cycle}")
        if pack in resolved:
            if not _ok(resolved[pack], constraint):
                raise _conflict(pack, constraint, requirer, resolved[pack])
            return
        stack.append(pack)
        try:
            options = _candidates(pack, constraint)
            if not options:
                raise _conflict(pack, constraint, requirer, None)
            last_error: ValueError | None = None
            for version in options:
                resolved[pack] = version
                try:
                    deps_map = available.get(pack, {}).get("deps", {})
                    deps = deps_map.get(version, []) if isinstance(deps_map, dict) else []
                    for entry in deps:
                        dep_name, dep_constraint = parse_dep(entry)
                        _visit(dep_name, dep_constraint, f"{pack}@{version}")
                except ValueError as exc:
                    last_error = exc
                    resolved.pop(pack, None)
                else:
                    return
            raise last_error or _conflict(pack, constraint, requirer, None)
        finally:
            if stack and stack[-1] == pack:
                stack.pop()

    def _conflict(pack: str, constraint: str, requirer: str, current: str | None) -> ValueError:
        lines = [f"version conflict installing {pack} (constraint {constraint!r} from {requirer}):"]
        for other in requirers.get(pack, []):
            if other != requirer:
                lines.append(f"  {other} requires an incompatible version of {pack}")
        if current:
            lines.append(f"  already resolved {pack}@{current}")
        lines.append(
            f"Options: pin an exact version ({pack}@<version>), "
            "re-run with --upgrade, or install an older requirer"
        )
        return ValueError("\n".join(lines))

    for name, constraint in requested.items():
        _visit(name, constraint, "root request")
    return resolved


def write_lockfile(
    store: str, resolved: dict[str, str], sources: dict[str, str] | None = None
) -> Path:
    """Write ``packs.lock.yaml`` at the store root. Returns the path."""
    sources = sources or {}
    payload: dict[str, Any] = {
        "version": 1,
        "resolved": {
            name: {"version": version, "source": sources.get(name, "")}
            for name, version in sorted(resolved.items())
        },
    }
    path = Path(store) / LOCKFILE
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def read_lockfile(store: str) -> dict[str, str]:
    """Read ``packs.lock.yaml`` → {name: version}. Empty dict when absent."""
    path = Path(store) / LOCKFILE
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    resolved = raw.get("resolved", {}) if isinstance(raw, dict) else {}
    return {str(k): str(v.get("version", "")) for k, v in resolved.items() if isinstance(v, dict)}
