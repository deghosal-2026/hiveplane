"""Plugin hooks for community extension (M56-06/07)."""

from hiveplane.plugins.loader import PluginLoadError, load_hook, load_plugins
from hiveplane.plugins.registry import (
    HookKind,
    PluginFailure,
    PluginHook,
    PluginRegistry,
)

__all__ = [
    "HookKind",
    "PluginFailure",
    "PluginHook",
    "PluginLoadError",
    "PluginRegistry",
    "load_hook",
    "load_plugins",
]
