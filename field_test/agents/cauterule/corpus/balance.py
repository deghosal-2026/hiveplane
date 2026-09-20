"""Corpus source-balance checks (#707).

Every external corpus-sourcing effort (#699-#706) adds *failure* trajectories.
The safety-gate result (100% silence) is only stress-tested if those same
sources also contribute matching *success* trajectories, so this module tracks
per-source success/failure counts and flags failure-only sources.

``source_repo`` / ``source`` are corpus-file metadata not modeled on
:class:`cauterule.models.trajectory.Trajectory`, so this module reads raw JSONL
records.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PUBLIC_ROOT = _REPO_ROOT / "corpus" / "public"


@dataclass(frozen=True)
class SourceBalance:
    """Success/failure counts for one corpus source."""

    source: str
    successes: int
    failures: int

    @property
    def balanced(self) -> bool:
        """True when the source has both a success and a failure trajectory."""
        return self.successes > 0 and self.failures > 0


def iter_jsonl_records(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    """Yield every parsed JSON object across *paths*."""
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def source_balance(records: Iterable[dict[str, Any]]) -> list[SourceBalance]:
    """Aggregate per-source success/failure counts from *records*."""
    counts: dict[str, list[int]] = {}
    for record in records:
        source = record.get("source_repo") or record.get("source")
        if not source or not isinstance(source, str):
            continue
        bucket = counts.setdefault(source, [0, 0])
        if record.get("success"):
            bucket[0] += 1
        else:
            bucket[1] += 1
    return [
        SourceBalance(source=source, successes=successes, failures=failures)
        for source, (successes, failures) in sorted(counts.items())
    ]


def balance_violations(balances: Iterable[SourceBalance]) -> list[str]:
    """Return human-readable violations for failure-only / success-only sources."""
    violations: list[str] = []
    for balance in balances:
        if balance.balanced:
            continue
        violations.append(
            f"{balance.source}: successes={balance.successes} failures={balance.failures} "
            "(needs both — pair failure corpus with matching success trajectories)"
        )
    return violations


def load_public_balance(public_root: str | Path | None = None) -> list[SourceBalance]:
    """Load source balance across all ``corpus/public/**/*.jsonl`` files."""
    root = Path(public_root) if public_root is not None else DEFAULT_PUBLIC_ROOT
    return source_balance(iter_jsonl_records(sorted(root.rglob("*.jsonl"))))
