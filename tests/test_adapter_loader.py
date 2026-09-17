"""Tests for entrypoint resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from hiveplane.adapters.errors import EntrypointLoadError
from hiveplane.adapters.loader import EntrypointLoader


def _write_module(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")


def test_loads_a_callable(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_ok", "def run(task, ctx):\n    return {'ok': True}\n")
    entry = EntrypointLoader(root=tmp_path).load("worker_ok:run")
    assert entry({"a": 1}, None) == {"ok": True}  # type: ignore[arg-type]


def test_unknown_module_raises(tmp_path: Path) -> None:
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader(root=tmp_path).load("worker_missing:run")


def test_unknown_attribute_raises(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_noattr", "def other(task, ctx):\n    return None\n")
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader(root=tmp_path).load("worker_noattr:run")


def test_non_callable_attribute_raises(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_const", "run = 3\n")
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader(root=tmp_path).load("worker_const:run")


def test_malformed_entrypoint_raises() -> None:
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader().load("not-an-entrypoint")


def test_load_object_returns_non_callable_attribute(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_graph", "graph = object()\n")
    loaded = EntrypointLoader(root=tmp_path).load_object("worker_graph:graph")
    assert loaded is not None
    assert not callable(loaded)


def test_load_object_can_return_a_callable(tmp_path: Path) -> None:
    _write_module(tmp_path, "worker_ok2", "def run(task, ctx):\n    return task\n")
    assert callable(EntrypointLoader(root=tmp_path).load_object("worker_ok2:run"))


def test_load_object_rejects_malformed_entrypoint() -> None:
    with pytest.raises(EntrypointLoadError):
        EntrypointLoader().load_object("nope")

