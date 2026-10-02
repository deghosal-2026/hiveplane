"""Plugin hooks: custom trigger sources, fan-out channels, policy checks (M56-06/07).

Plugins are arbitrary code. Hooks run in a guarded context: every invocation is
wrapped so an exception, timeout, or bad return value is recorded and swallowed —
a plugin can never crash the control plane or block a critical path.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HookKind(StrEnum):
    """The extension points a plugin may implement."""

    TRIGGER_SOURCE = "trigger_source"
    FANOUT_CHANNEL = "fanout_channel"
    POLICY_CHECK = "policy_check"


class PluginHook(BaseModel):
    """A declared plugin hook (name + kind + entrypoint)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    kind: HookKind
    entrypoint: str = Field(min_length=1)


class PluginFailure(BaseModel):
    """A recorded plugin failure (never propagated to the caller)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    error: str


class PluginRegistry:
    """Registers plugin hooks and invokes them without risking the plane."""

    def __init__(self) -> None:
        self._hooks: dict[str, tuple[HookKind, Callable[..., Any]]] = {}
        self._failures: list[PluginFailure] = []

    def register(self, name: str, kind: HookKind, hook: Callable[..., Any]) -> None:
        """Register a callable hook under a name."""
        if not callable(hook):
            raise TypeError(f"plugin hook {name!r} is not callable")
        self._hooks[name] = (kind, hook)

    def names(self, kind: HookKind | None = None) -> list[str]:
        """Return registered hook names, optionally filtered by kind."""
        return sorted(
            name
            for name, (hook_kind, _) in self._hooks.items()
            if kind is None or hook_kind is kind
        )

    def failures(self) -> list[PluginFailure]:
        """Return recorded plugin failures."""
        return list(self._failures)

    def invoke(self, name: str, *args: Any, **kwargs: Any) -> Any | None:
        """Invoke a hook, returning ``None`` if it is missing or fails.

        Failures are recorded, never raised, so callers on a critical path can
        treat a plugin as best-effort.
        """
        entry = self._hooks.get(name)
        if entry is None:
            self._failures.append(PluginFailure(name=name, error="hook is not registered"))
            return None
        _, hook = entry
        try:
            return hook(*args, **kwargs)
        except Exception as exc:  # plugins are untrusted; never propagate
            self._failures.append(PluginFailure(name=name, error=str(exc)))
            return None

    def check(self, name: str, context: dict[str, Any]) -> dict[str, Any] | None:
        """Invoke policy-check hooks, discarding non-mapping returns."""
        result = self.invoke(name, context)
        return result if isinstance(result, dict) else None
