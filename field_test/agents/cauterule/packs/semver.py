"""Semantic versioning for rule packs (#548).

Strict ``MAJOR.MINOR.PATCH`` with optional ``-rc.N`` prerelease suffix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")


@dataclass(frozen=True)
class Version:
    """Parsed semver version."""

    major: int
    minor: int
    patch: int
    rc: int | None = None

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-rc.{self.rc}" if self.rc is not None else base

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def _key(self) -> tuple[int, int, int, int, int]:
        # Release > any rc of the same triple: release sorts last.
        rc_flag = 1 if self.rc is None else 0
        return (self.major, self.minor, self.patch, rc_flag, self.rc or 0)

    @property
    def is_prerelease(self) -> bool:
        """Return True for ``-rc.N`` versions."""
        return self.rc is not None


def parse(version: str) -> Version:
    """Parse a strict semver string. Raises ValueError if invalid."""
    match = _SEMVER_RE.match(version.strip())
    if not match:
        raise ValueError(f"invalid semver {version!r}: want MAJOR.MINOR.PATCH (optional -rc.N)")
    major, minor, patch, rc = match.groups()
    return Version(int(major), int(minor), int(patch), int(rc) if rc else None)


def bump(version: str, kind: str) -> str:
    """Bump *version* by kind (major/minor/patch). Resets prerelease."""
    if kind not in ("major", "minor", "patch"):
        raise ValueError(f"--bump must be major|minor|patch, got {kind!r}")
    current = parse(version)
    if kind == "major":
        return str(Version(current.major + 1, 0, 0))
    if kind == "minor":
        return str(Version(current.major, current.minor + 1, 0))
    return str(Version(current.major, current.minor, current.patch + 1))


def suggest_bump(old_rules: list[str], new_rules: list[str]) -> str:
    """Suggest a bump kind by diffing rule id lists (warn-only policy)."""
    old, new = set(old_rules), set(new_rules)
    if old - new:
        return "major"  # removals / narrowing coverage
    if new - old:
        return "minor"  # new rules
    return "patch"  # text-only or metadata fixes


def _caret_upper(v: Version) -> Version:
    if v.major > 0:
        return Version(v.major + 1, 0, 0)
    if v.minor > 0:
        return Version(0, v.minor + 1, 0)
    return Version(0, 0, v.patch + 1)


def _tilde_upper(v: Version) -> Version:
    if v.major > 0 or v.minor > 0:
        return Version(v.major, v.minor + 1, 0)
    return Version(0, 0, v.patch + 1)


def satisfies(version: str, constraint: str) -> bool:
    """Return True if *version* satisfies a comma-joined constraint.

    Supports ``^ ~ >= <= > < ==`` prefixes and bare versions (exact).
    Prereleases never satisfy an unpinned constraint.
    """
    candidate = parse(version)
    # Prereleases only satisfy constraints that name them.
    if (
        candidate.is_prerelease
        and "@" not in constraint
        and not any("-rc" in part for part in constraint.split(","))
    ):
        return False
    for part in constraint.split(","):
        part = part.strip()
        if not part:
            continue
        matched = re.match(r"^(>=|<=|==|>|<|\^|~)?\s*(.+)$", part)
        if not matched:
            raise ValueError(f"invalid version constraint {part!r}")
        operator, ref = matched.groups()
        target = parse(ref)
        if operator in (None, "", "=="):
            if candidate != target:
                return False
        elif operator == ">=":
            if not candidate._key() >= target._key():
                return False
        elif operator == "<=":
            if not candidate._key() <= target._key():
                return False
        elif operator == ">":
            if not candidate._key() > target._key():
                return False
        elif operator == "<":
            if not candidate._key() < target._key():
                return False
        elif operator == "^":
            upper = _caret_upper(target)._key()
            if not (candidate._key() >= target._key() and candidate._key() < upper):
                return False
        elif operator == "~":
            upper = _tilde_upper(target)._key()
            if not (candidate._key() >= target._key() and candidate._key() < upper):
                return False
    return True
