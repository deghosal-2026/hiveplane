"""Import-boundary test: core must not import framework/provider SDKs (M31, D25).

Adapters and the provider seam may import their frameworks; every other package
must depend only on HivePlane DTOs, so HivePlane does not become a framework
wrapper (DD-02). This AST scan fails on a forbidden top-level import anywhere
under ``src/hiveplane`` outside ``hiveplane.adapters`` and ``hiveplane.llm``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "hiveplane"

#: Packages allowed to import framework/provider SDKs.
_ALLOWED = {"adapters", "llm"}

#: Framework-specific integration modules that are not core (LangGraph checkpointer).
_ALLOWED_FILES = {"checkpointing.py"}

#: Top-level modules that must never leak into core.
_FORBIDDEN = {
    "langgraph",
    "langchain",
    "langchain_core",
    "pydantic_ai",
    "pydantic_graph",
    "agents",
    "crewai",
    "openai",
    "anthropic",
    "litellm",
    "google",
}


def _imported_roots(tree: ast.AST) -> list[tuple[str, int]]:
    roots: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend((alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append((node.module.split(".")[0], node.lineno))
    return roots


def _core_modules() -> list[Path]:
    modules: list[Path] = []
    for path in _SRC.rglob("*.py"):
        relative = path.relative_to(_SRC)
        if relative.parts[0] in _ALLOWED or relative.name in _ALLOWED_FILES:
            continue
        modules.append(path)
    return modules


def test_core_does_not_import_framework_sdks() -> None:
    violations: list[str] = []
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for root, lineno in _imported_roots(tree):
            if root in _FORBIDDEN:
                violations.append(f"{path.relative_to(_ROOT)}:{lineno} imports {root!r}")
    assert not violations, "framework SDKs leaked into core:\n" + "\n".join(violations)


def test_adapters_may_import_frameworks() -> None:
    # The boundary is one-directional: adapters are where framework imports live.
    adapter_modules = list((_SRC / "adapters").glob("*.py"))
    assert adapter_modules, "expected adapter modules to exist"
