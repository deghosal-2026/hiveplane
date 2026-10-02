"""Load plugin hooks from declared entrypoints (M56-06).

Import is restricted to the configured plugin directory (when set) so a hook
entrypoint cannot reach arbitrary installed modules by default.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from hiveplane.plugins.registry import PluginHook, PluginRegistry


class PluginLoadError(RuntimeError):
    """Raised when a plugin hook entrypoint cannot be loaded."""


def load_hook(entrypoint: str, *, plugins_dir: str | None = None) -> Callable[..., Any]:
    """Resolve a ``module:attribute`` entrypoint to a callable.

    When ``plugins_dir`` is set, only modules resolvable under that directory are
    loaded (a best-effort restriction, not a security sandbox).
    """
    if ":" not in entrypoint:
        raise PluginLoadError(f"entrypoint must be 'module:attribute': {entrypoint!r}")
    module_name, _, attribute = entrypoint.partition(":")
    if plugins_dir is not None:
        root = Path(plugins_dir).resolve()
        candidate = (root / module_name.replace(".", "/")).with_suffix(".py")
        if not candidate.is_file():
            raise PluginLoadError(
                f"plugin module {module_name!r} is not under {str(root)!r}"
            )
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise PluginLoadError(f"cannot import plugin module {module_name!r}: {exc}") from exc
    hook = getattr(module, attribute, None)
    if hook is None or not callable(hook):
        raise PluginLoadError(
            f"plugin entrypoint {entrypoint!r} is not a callable attribute"
        )
    return cast("Callable[..., Any]", hook)


def load_plugins(
    registry: PluginRegistry,
    hooks: Sequence[PluginHook],
    *,
    plugins_dir: str | None = None,
) -> list[str]:
    """Register every loadable hook; return the names of hooks that failed to load.

    A single bad plugin never blocks the others or the plane.
    """
    failed: list[str] = []
    for hook in hooks:
        try:
            callable_hook = load_hook(hook.entrypoint, plugins_dir=plugins_dir)
        except PluginLoadError:
            failed.append(hook.name)
            continue
        registry.register(hook.name, hook.kind, callable_hook)
    return failed
