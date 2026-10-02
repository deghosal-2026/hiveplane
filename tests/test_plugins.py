"""Tests for plugin hooks and plugin safety (M56-06/07)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hiveplane.plugins.loader import PluginLoadError, load_hook, load_plugins
from hiveplane.plugins.registry import HookKind, PluginHook, PluginRegistry

_PLUGINS_DIR = Path(__file__).resolve().parents[1] / "examples" / "plugins"


def test_registry_registers_and_invokes() -> None:
    registry = PluginRegistry()
    registry.register("echo", HookKind.FANOUT_CHANNEL, lambda value: value * 2)

    assert registry.names(HookKind.FANOUT_CHANNEL) == ["echo"]
    assert registry.invoke("echo", 3) == 6
    assert registry.failures() == []


def test_a_failing_hook_is_recorded_not_raised() -> None:
    registry = PluginRegistry()

    def boom() -> None:
        raise RuntimeError("plugin exploded")

    registry.register("boom", HookKind.POLICY_CHECK, boom)

    assert registry.invoke("boom") is None
    assert registry.failures()[0].name == "boom"
    assert "exploded" in registry.failures()[0].error


def test_missing_hook_is_recorded() -> None:
    registry = PluginRegistry()

    assert registry.invoke("ghost") is None
    assert registry.failures()[0].name == "ghost"


def test_policy_check_discards_non_mapping() -> None:
    registry = PluginRegistry()
    registry.register("bad", HookKind.POLICY_CHECK, lambda context: ["not", "a", "map"])

    assert registry.check("bad", {}) is None


def test_register_rejects_non_callable() -> None:
    registry = PluginRegistry()

    with pytest.raises(TypeError):
        registry.register("x", HookKind.TRIGGER_SOURCE, "not callable")  # type: ignore[arg-type]


def test_load_hook_resolves_a_sample_plugin() -> None:
    hook = load_hook("sample_plugin:relay", plugins_dir=str(_PLUGINS_DIR))

    assert hook("slack", {"type": "run.completed"}) == {
        "relayed_to": "slack",
        "type": "run.completed",
    }


def test_load_hook_rejects_bad_entrypoints() -> None:
    with pytest.raises(PluginLoadError):
        load_hook("no_colon", plugins_dir=str(_PLUGINS_DIR))
    with pytest.raises(PluginLoadError):
        load_hook("sample_plugin:missing", plugins_dir=str(_PLUGINS_DIR))


def test_load_hook_restricts_to_the_plugins_directory() -> None:
    with pytest.raises(PluginLoadError):
        load_hook("json:loads", plugins_dir=str(_PLUGINS_DIR))


def test_load_plugins_registers_valid_and_reports_failed() -> None:
    registry = PluginRegistry()
    hooks = [
        PluginHook(name="relay", kind=HookKind.FANOUT_CHANNEL, entrypoint="sample_plugin:relay"),
        PluginHook(name="broken", kind=HookKind.POLICY_CHECK, entrypoint="nope:nope"),
    ]

    failed = load_plugins(registry, hooks, plugins_dir=str(_PLUGINS_DIR))

    assert failed == ["broken"]
    assert registry.names() == ["relay"]


def test_sample_policy_hook_fires_without_destabilizing_the_plane() -> None:
    registry = PluginRegistry()
    load_plugins(
        registry,
        [
            PluginHook(
                name="allow", kind=HookKind.POLICY_CHECK, entrypoint="sample_plugin:allow_all"
            )
        ],
        plugins_dir=str(_PLUGINS_DIR),
    )

    assert registry.check("allow", {"workload": "demo"}) == {
        "outcome": "allow",
        "reason": "sample plugin allows all",
    }
