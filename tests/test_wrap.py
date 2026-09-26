"""Tests for `hiveplane wrap` detection and scaffolding (M31-05, M31-06)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hiveplane.core.manifest import load_manifest
from hiveplane.wrap import WrapError, plan_wrap, write_wrap
from hiveplane.wrap.detect import detect

_LANGGRAPH_APP = """
from langgraph.graph import END, START, StateGraph


def build_graph():
    builder = StateGraph(dict)
    builder.add_edge(START, END)
    return builder.compile()


graph = build_graph()
"""

_PYDANTICAI_APP = """
from pydantic_ai import Agent


def build_agent():
    return Agent("test")


agent = build_agent()
"""

_OPENAI_APP = """
from agents import Agent


def create_agent():
    return Agent(name="assistant")


app = create_agent()
"""


def _write_app(root: Path, source: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(source, encoding="utf-8")
    return root


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("source", "framework", "entrypoint"),
    [
        (_LANGGRAPH_APP, "langgraph", "app:graph"),
        (_PYDANTICAI_APP, "pydanticai", "app:agent"),
        (_OPENAI_APP, "openai-agents", "app:app"),
    ],
)
def test_detect_framework_and_entrypoint(
    tmp_path: Path, source: str, framework: str, entrypoint: str
) -> None:
    app = _write_app(tmp_path / "src", source)
    detection = detect(app)
    assert detection.framework == framework
    assert detection.entrypoint == entrypoint


def test_detect_rejects_unknown_framework(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "plain", "import json\n")
    with pytest.raises(WrapError):
        detect(app)


def test_detect_missing_path() -> None:
    with pytest.raises(WrapError):
        detect("/nonexistent/path/xyz")


def test_explicit_framework_overrides_detection(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "plain", "def build():\n    return 1\n")
    detection = detect(app, framework="langgraph")
    assert detection.framework == "langgraph"
    assert detection.entrypoint == "app:build"


def test_plan_generates_manifest_scaffold_and_corpus(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "src", _LANGGRAPH_APP)
    plan = plan_wrap(app)
    assert plan.framework == "langgraph"
    assert set(plan.files) == {
        "workload.yaml",
        "adapter_scaffold.py",
        "corpus.template.yaml",
        "README.md",
    }
    assert "adapter: langgraph" in plan.files["workload.yaml"]
    assert "from app import graph as entrypoint" in plan.files["adapter_scaffold.py"]


def test_write_wrap_does_not_modify_source(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "src", _LANGGRAPH_APP)
    before = _snapshot(app)
    plan = plan_wrap(app)
    written = write_wrap(plan, tmp_path / "out")
    assert {path.name for path in written} == set(plan.files)
    assert _snapshot(app) == before, "wrap modified the source tree"


def test_generated_manifest_is_valid(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "src", _PYDANTICAI_APP)
    plan = plan_wrap(app)
    write_wrap(plan, tmp_path / "out")
    workload = load_manifest(tmp_path / "out" / "workload.yaml")
    assert workload.spec.runtime.adapter.value == "pydanticai"
    assert workload.spec.runtime.adapter_contract == "2"
    assert workload.certification_status.value == "uncertified"


def test_write_refuses_source_tree(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "src", _LANGGRAPH_APP)
    plan = plan_wrap(app)
    with pytest.raises(WrapError):
        write_wrap(plan, app)
    with pytest.raises(WrapError):
        write_wrap(plan, app / "nested")


def test_write_refuses_nonempty_without_force(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "src", _LANGGRAPH_APP)
    plan = plan_wrap(app)
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(WrapError):
        write_wrap(plan, out)
    written = write_wrap(plan, out, force=True)
    assert written


def test_wrap_is_deterministic(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "src", _LANGGRAPH_APP)
    first = plan_wrap(app)
    second = plan_wrap(app)
    assert first.files == second.files


def test_detect_reports_syntax_error(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "broken", "def (:\n")
    with pytest.raises(WrapError):
        detect(app)


def test_detect_factory_function(tmp_path: Path) -> None:
    app = _write_app(
        tmp_path / "factory",
        "from pydantic_ai import Agent\n\n\ndef create_agent():\n    return Agent('t')\n",
    )
    detection = detect(app)
    assert detection.framework == "pydanticai"
    assert detection.entrypoint == "app:create_agent"


def test_detect_annotated_assignment(tmp_path: Path) -> None:
    app = _write_app(
        tmp_path / "annotated",
        "from typing import Any\nfrom langgraph.graph import StateGraph\n\ngraph: Any = None\n",
    )
    detection = detect(app)
    assert detection.framework == "langgraph"
    assert detection.entrypoint == "app:graph"


def test_detect_skips_virtualenvs(tmp_path: Path) -> None:
    app = tmp_path / "proj"
    (app / ".venv").mkdir(parents=True)
    (app / ".venv" / "lib.py").write_text("import crewai\n", encoding="utf-8")
    _write_app(app, "import json\n")
    with pytest.raises(WrapError):
        detect(app)


def test_detect_no_entrypoint_when_no_candidates(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "nocand", "import langgraph\n\nX = 1\n")
    detection = detect(app)
    assert detection.framework == "langgraph"
    assert detection.entrypoint is None


def test_write_refuses_ancestor_of_source(tmp_path: Path) -> None:
    app = _write_app(tmp_path / "deep" / "src", _LANGGRAPH_APP)
    plan = plan_wrap(app)
    with pytest.raises(WrapError):
        write_wrap(plan, tmp_path / "deep")
