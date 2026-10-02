"""Static framework detection for ``hiveplane wrap`` (M31-05).

Detection is AST-only: it never imports or executes the target app, so wrapping
an untrusted app cannot run its code. It finds the framework from imports and
guesses the entrypoint from top-level names and factory functions.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

#: Import root -> adapter value.
_FRAMEWORK_IMPORTS: dict[str, str] = {
    "langgraph": "langgraph",
    "langchain_core": "langgraph",
    "langchain": "langgraph",
    "pydantic_ai": "pydanticai",
    "pydantic_graph": "pydanticai",
    "agents": "openai-agents",
    "crewai": "crewai",
}

#: Top-level assignment names that usually hold the runnable object.
_OBJECT_NAMES = ("graph", "agent", "app", "workflow", "crew", "team", "runner")
#: Function-name prefixes that usually build the runnable object.
_FACTORY_PREFIXES = ("build", "make", "create", "get", "load", "compile")
#: Function names that are common entrypoints.
_ENTRYPOINT_NAMES = ("main", "run", "entrypoint", "graph", "agent")

_SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", ".mypy_cache", ".pytest_cache"}


class WrapError(Exception):
    """Raised when wrap cannot detect a framework or a target entrypoint."""


@dataclass
class Detection:
    """What the AST scan found in a source tree."""

    source_root: Path
    framework: str | None = None
    entrypoint: str | None = None
    modules: list[str] = field(default_factory=list)
    imported_frameworks: set[str] = field(default_factory=set)


def _iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return ".".join(relative.parts)


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _entrypoint_candidates(tree: ast.Module) -> list[str]:
    candidates: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.lower() in _OBJECT_NAMES:
                    candidates.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.lower() in _OBJECT_NAMES:
                candidates.append(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name.lower()
            if name in _ENTRYPOINT_NAMES or name.startswith(_FACTORY_PREFIXES):
                candidates.append(node.name)
    return candidates


def detect(path: str | Path, *, framework: str | None = None) -> Detection:
    """Detect the framework and a best-guess entrypoint under ``path``.

    ``framework`` (an adapter value) overrides detection; when it is ``None`` and
    no supported framework import is found, a :class:`WrapError` is raised.
    """
    root = Path(path).resolve()
    if not root.exists():
        raise WrapError(f"path {path!r} does not exist")
    detection = Detection(source_root=root)
    best: tuple[int, str] | None = None

    for file_path in _iter_python_files(root):
        module = _module_name(root, file_path)
        detection.modules.append(module)
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        except SyntaxError as exc:
            raise WrapError(f"cannot parse {file_path}: {exc}") from exc
        roots = _imported_roots(tree)
        detection.imported_frameworks.update(
            adapter for name, adapter in _FRAMEWORK_IMPORTS.items() if name in roots
        )
        for candidate in _entrypoint_candidates(tree):
            score = _score(candidate)
            if best is None or score > best[0]:
                best = (score, f"{module}:{candidate}")

    if framework is not None:
        detection.framework = framework
    elif detection.imported_frameworks:
        detection.framework = sorted(detection.imported_frameworks)[0]
    else:
        raise WrapError(
            "no supported framework detected (langgraph, pydantic_ai, agents, crewai)"
        )

    if best is not None:
        detection.entrypoint = best[1]
    return detection


def _score(name: str) -> int:
    lowered = name.lower()
    if lowered in ("graph", "agent", "app", "crew", "team"):
        return 3
    if lowered.startswith(("build", "make", "create")):
        return 2
    if lowered in _ENTRYPOINT_NAMES or lowered.startswith(("get", "load", "compile")):
        return 1
    return 0
