"""Tests for workload-derived tool seeding (M23, #131)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hiveplane.core.manifest import load_manifest
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.seeding import (
    SeedResult,
    derive_tool_registrations,
    seed_tools,
)
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_WORKLOADS_DIR = Path(__file__).resolve().parents[1] / "examples" / "workloads"


def _registry() -> RegistryService:
    return RegistryService(InMemoryRegistryStore(), clock=lambda: _NOW)


def _manifest(make_manifest: Callable[..., AgentWorkload], **tools: object) -> AgentWorkload:
    return make_manifest(name="repo-agent", tools=tools)


def test_derive_tool_registrations_strips_the_mcp_prefix(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    manifest = _manifest(
        make_manifest,
        allow=[{"tool_id": "mcp.github.list_pull_requests", "trust_level": "read_only"}],
    )

    registrations = derive_tool_registrations(manifest)

    assert len(registrations) == 1
    registration = registrations[0]
    assert registration.tool_id == "mcp.github.list_pull_requests"
    assert registration.name == "list_pull_requests"
    assert registration.mcp_server == "github"
    assert registration.trust_level is ToolTrustLevel.READ_ONLY


def test_derive_tool_registrations_handles_ids_without_the_mcp_prefix(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    manifest = _manifest(
        make_manifest,
        allow=[{"tool_id": "pagerduty.acknowledge", "trust_level": "destructive"}],
    )

    registration = derive_tool_registrations(manifest)[0]

    assert registration.name == "acknowledge"
    assert registration.mcp_server == "pagerduty"
    assert registration.trust_level is ToolTrustLevel.DESTRUCTIVE


def test_seed_tools_registers_missing_tools(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry = _registry()
    manifest = _manifest(
        make_manifest,
        allow=[{"tool_id": "mcp.github.read_issue", "trust_level": "read_only"}],
    )

    result = seed_tools(registry, derive_tool_registrations(manifest), clock=lambda: _NOW)

    assert isinstance(result, SeedResult)
    assert result.registered == ["mcp.github.read_issue"]
    assert result.existing == []
    assert registry.get_tool("mcp.github.read_issue").trust_level is ToolTrustLevel.READ_ONLY


def test_seed_tools_is_idempotent(make_manifest: Callable[..., AgentWorkload]) -> None:
    registry = _registry()
    manifest = _manifest(
        make_manifest,
        allow=[{"tool_id": "mcp.github.read_issue", "trust_level": "read_only"}],
    )
    registrations = derive_tool_registrations(manifest)

    first = seed_tools(registry, registrations, clock=lambda: _NOW)
    second = seed_tools(registry, registrations, clock=lambda: _NOW)

    assert first.registered == ["mcp.github.read_issue"]
    assert second.registered == []
    assert second.existing == ["mcp.github.read_issue"]


def test_seed_tools_registers_destructive_trust(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry = _registry()
    manifest = _manifest(
        make_manifest,
        allow=[{"tool_id": "pagerduty.acknowledge", "trust_level": "destructive"}],
    )

    seed_tools(registry, derive_tool_registrations(manifest), clock=lambda: _NOW)

    tool = registry.get_tool("pagerduty.acknowledge")
    assert tool.trust_level is ToolTrustLevel.DESTRUCTIVE


def test_seeded_tools_satisfy_workload_registration(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry = _registry()
    manifest = _manifest(
        make_manifest,
        allow=[
            {"tool_id": "mcp.github.read_issue", "trust_level": "read_only"},
            {"tool_id": "mcp.github.create_pr", "trust_level": "destructive",
             "require_approval": True},
        ],
    )
    seed_tools(registry, derive_tool_registrations(manifest), clock=lambda: _NOW)

    registry.create(manifest)

    assert registry.get(manifest.name).manifest.spec.tools.allow


def test_every_example_workload_registers_after_seeding() -> None:
    registry = _registry()
    manifests = [
        load_manifest(_WORKLOADS_DIR / f"{name}.yaml")
        for name in ("repo-agent", "docs-agent", "incident-agent")
    ]
    registrations = [
        registration
        for manifest in manifests
        for registration in derive_tool_registrations(manifest)
    ]

    seed_tools(registry, registrations, clock=lambda: _NOW)
    for manifest in manifests:
        registry.create(manifest)

    registered = {tool.tool_id for tool in registry.list_tools()}
    for manifest in manifests:
        for entry in manifest.spec.tools.allow:
            assert entry.tool_id in registered, f"{manifest.name}: {entry.tool_id} missing"
