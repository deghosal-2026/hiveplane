"""Runtime entrypoint resolution (M16).

A manifest declares ``runtime.entrypoint`` as ``module:function``. The loader
imports the module with the configured root on ``sys.path`` and returns the
callable, failing fast and loudly when it cannot.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from hiveplane.adapters.errors import EntrypointLoadError
from hiveplane.adapters.worker import WorkerContext

#: A workload entrypoint: ``run(task, ctx) -> result``.
Entrypoint = Callable[[dict[str, JsonValue], WorkerContext], JsonValue]


@contextmanager
def _prepend_sys_path(root: str) -> Iterator[None]:
    sys.path.insert(0, root)
    try:
        yield
    finally:
        with suppress(ValueError):
            sys.path.remove(root)


class EntrypointLoader:
    """Resolves ``module:function`` entrypoints against a root directory."""

    def __init__(self, *, root: str | Path = ".") -> None:
        self._root = Path(root)

    def load(self, entrypoint: str) -> Entrypoint:
        """Import and return the callable named by ``entrypoint``."""
        module_name, _, attr = entrypoint.partition(":")
        if not module_name or not attr:
            raise EntrypointLoadError(entrypoint, "expected 'module:function'")
        with _prepend_sys_path(str(self._root)):
            try:
                module = importlib.import_module(module_name)
            except ImportError as exc:
                raise EntrypointLoadError(entrypoint, str(exc)) from exc
        target = getattr(module, attr, None)
        if target is None:
            raise EntrypointLoadError(entrypoint, f"module has no attribute {attr!r}")
        if not callable(target):
            raise EntrypointLoadError(entrypoint, f"attribute {attr!r} is not callable")
        return cast("Entrypoint", target)
