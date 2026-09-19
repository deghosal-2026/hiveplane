"""Fixture-backed tool execution for sandboxed and benchmark runs (M23, #116).

Agents do not fabricate tool output: the adapter executes the tool and the
boundary shapes the real result. In CI and benchmarks the tool call is served
from a JSON fixture under ``deploy/testdata/tools/``.

Fixture filenames keep the dots in a tool id (``mcp.github.read_issue`` ->
``mcp.github.read_issue.json``); any character outside ``[A-Za-z0-9._-]`` is
replaced with ``_`` and the resolved path must stay under the fixtures root.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

_SAFE = re.compile(r"[^A-Za-z0-9._-]")

#: Default location of fixture-backed tool outputs, relative to the repo root.
DEFAULT_TOOL_FIXTURES = "deploy/testdata/tools"


class ToolExecutionError(Exception):
    """Base class for tool execution failures."""


class ToolFixtureNotFoundError(ToolExecutionError):
    """Raised when no fixture exists for a requested tool id."""

    def __init__(self, tool_id: str, path: Path) -> None:
        super().__init__(f"no fixture for tool {tool_id!r} at {str(path)!r}")
        self.tool_id = tool_id
        self.path = path


class ToolExecutor(Protocol):
    """Executes a tool and returns its raw output."""

    def execute(self, tool_id: str) -> str: ...


class FixtureToolExecutor:
    """Serves tool output from JSON fixtures on disk."""

    def __init__(self, root: str | Path = DEFAULT_TOOL_FIXTURES) -> None:
        self._root = Path(root)

    def execute(self, tool_id: str) -> str:
        """Return the fixture for ``tool_id`` as a JSON string."""
        path = self._path_for(tool_id)
        if not path.is_file():
            raise ToolFixtureNotFoundError(tool_id, path)
        return path.read_text(encoding="utf-8")

    def _path_for(self, tool_id: str) -> Path:
        root = self._root.resolve()
        sanitized = _SAFE.sub("_", tool_id)
        candidate = (root / f"{sanitized}.json").resolve()
        if not candidate.is_relative_to(root):
            raise ToolFixtureNotFoundError(tool_id, candidate)
        return candidate
