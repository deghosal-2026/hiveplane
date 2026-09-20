"""JSONL serialization for Trajectory.

Each trajectory is stored as a single JSON object per line, streamable.
Also supports pretty-printed multi-line JSON objects (#490).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from cauterule.log import get_logger
from cauterule.models.trajectory import Trajectory

_log = get_logger(__name__)


def dump_trajectory(trajectory: Trajectory) -> str:
    """Serialize *trajectory* to a JSON string (one line)."""
    return json.dumps(trajectory.to_dict(), ensure_ascii=False)


def load_trajectory(line: str) -> Trajectory:
    """Deserialize a JSON line to a :class:`Trajectory`."""
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("JSONL line must decode to a mapping")
    return Trajectory.from_dict(data)


def dump_trajectories(trajectories: Iterable[Trajectory], path: str | Path) -> None:
    """Write *trajectories* to *path* as JSONL (one per line)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for t in trajectories:
            f.write(dump_trajectory(t) + "\n")


def _json_depth(line: str) -> int:
    """Count brace depth outside of JSON string values (#490)."""
    depth = 0
    in_string = False
    escaped = False
    for ch in line:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"' and not escaped:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth


def load_trajectories(
    path: str | Path,
    *,
    strict: bool = False,
    on_skip: Callable[[int, str], None] | None = None,
) -> Iterator[Trajectory]:
    """Stream trajectories from a JSONL file at *path*.

    Skips blank lines. Supports both single-line and multi-line (pretty-
    printed) JSON objects (#490). Multi-line objects are detected by a
    lone ``{`` on its own line; accumulation continues until a matching
    ``}`` closes the brace depth.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        buf: str | None = None
        depth = 0
        lineno = 0
        for lineno, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            if buf is not None:
                buf += "\n" + stripped
                depth += _json_depth(stripped)
                if depth <= 0:
                    try:
                        yield load_trajectory(buf)
                    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                        msg = f"{p}:{lineno}: multi-line JSON failed ({exc})"
                        if strict:
                            raise ValueError(msg) from exc
                        _log.warning(msg)
                        if on_skip is not None:
                            on_skip(lineno, str(exc))
                    buf = None
                continue

            # Detect multi-line JSON: a lone ``{`` on its own line.
            if stripped == "{":
                buf = stripped
                depth = 1
                continue

            # Single-line JSONL path.
            try:
                yield load_trajectory(stripped)
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                msg = f"{p}:{lineno}: skipping bad JSONL line ({exc})"
                if strict:
                    raise ValueError(msg) from exc
                _log.warning(msg)
                if on_skip is not None:
                    on_skip(lineno, str(exc))

        # #773: a multi-line record truncated at EOF must be reported, not
        # silently dropped — route it through the same strict/warn/on_skip path.
        if buf is not None:
            try:
                yield load_trajectory(buf)
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                msg = f"{p}:{lineno}: multi-line JSON truncated at EOF ({exc})"
                if strict:
                    raise ValueError(msg) from exc
                _log.warning(msg)
                if on_skip is not None:
                    on_skip(lineno, f"truncated at EOF: {exc}")


@dataclass(frozen=True)
class LoadResult:
    """Outcome of :func:`load_trajectories_result`."""

    loaded: list[Trajectory]
    skipped: int
    errors: list[str] = field(default_factory=list)


def load_trajectories_result(path: str | Path, *, strict: bool = False) -> LoadResult:
    """Load all trajectories from *path*, collecting skip info (#597)."""
    errors: list[str] = []

    def _record(lineno: int, error: str) -> None:
        errors.append(f"line {lineno}: {error}")

    loaded = list(load_trajectories(path, strict=strict, on_skip=_record))
    return LoadResult(loaded=loaded, skipped=len(errors), errors=errors)


def append_trajectory(trajectory: Trajectory, path: str | Path) -> None:
    """Append a single *trajectory* to a JSONL file at *path*."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(dump_trajectory(trajectory) + "\n")
