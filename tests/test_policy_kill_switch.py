"""Tests for the tool kill switch (M40-06)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import Engine

from hiveplane.config import Settings
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.tools import ToolCallOutcome, ToolCallRequest, ToolGateway
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.kill_switch import (
    InMemoryKillSwitchStore,
    KillSwitch,
    KillSwitchRecord,
    PostgresKillSwitchStore,
    build_kill_switch_store,
)
from hiveplane.policy.packs import InMemoryPolicyPackStore
from postgres import ensure_schema
from test_tool_gateway import _run, _Runs, _workload

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _kill_switch(audit: InMemoryAuditLog | None = None) -> KillSwitch:
    return KillSwitch(
        InMemoryKillSwitchStore(), audit=audit, clock=lambda: _NOW
    )


def test_disable_and_enable_are_audited() -> None:
    audit = InMemoryAuditLog()
    kill = _kill_switch(audit)

    disabled = kill.disable("mcp.t.destructive", actor="alice", reason="incident")
    assert disabled.disabled is True
    assert kill.is_disabled("mcp.t.destructive") is True

    kill.enable("mcp.t.destructive", actor="alice")
    assert kill.is_disabled("mcp.t.destructive") is False
    assert [record.action for record in audit.records()] == ["tool.disabled", "tool.enabled"]


def test_disabled_tools_lists_only_disabled() -> None:
    kill = _kill_switch()
    kill.disable("a", actor="alice")
    kill.disable("b", actor="alice")
    kill.enable("b", actor="alice")

    assert kill.disabled_tools() == ["a"]


def test_unknown_tool_is_not_disabled() -> None:
    assert _kill_switch().is_disabled("ghost") is False


def test_gateway_blocks_a_killed_tool(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    workload = _workload(make_manifest)
    kill = _kill_switch()
    kill.disable("mcp.t.read", actor="alice", reason="incident")
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(manifest=workload))
    gateway = ToolGateway(
        registry,  # type: ignore[arg-type]
        PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _NOW),
        _Runs(_run()),
        kill_switch=kill,
        clock=lambda: _NOW,
    )

    result = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.read"))

    assert result.outcome is ToolCallOutcome.DENIED
    assert result.rule == "kill_switch"


def test_gateway_allows_an_enabled_tool(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    workload = _workload(make_manifest)
    kill = _kill_switch()
    kill.disable("mcp.t.read", actor="alice")
    kill.enable("mcp.t.read", actor="alice")
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(manifest=workload))
    gateway = ToolGateway(
        registry,  # type: ignore[arg-type]
        PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _NOW),
        _Runs(_run()),
        kill_switch=kill,
        clock=lambda: _NOW,
    )

    result = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.read"))

    assert result.outcome is ToolCallOutcome.ALLOWED


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_kill_switch_store(Settings()), InMemoryKillSwitchStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_kill_switch_store(settings), PostgresKillSwitchStore)


def test_postgres_kill_switch_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresKillSwitchStore(pg_engine)
    store.clear()
    store.save(
        KillSwitchRecord(
            tool_id="mcp.t.destructive",
            disabled=True,
            reason="incident",
            actor="alice",
            changed_at=_NOW,
        )
    )

    reopened = PostgresKillSwitchStore(pg_engine)
    record = reopened.get("mcp.t.destructive")
    assert record is not None
    assert record.disabled is True
    assert reopened.list()[0].tool_id == "mcp.t.destructive"
    store.clear()
    assert reopened.get("mcp.t.destructive") is None



def test_tool_trust_enum_still_exposes_read_only() -> None:
    assert ToolTrustLevel.READ_ONLY.value == "read_only"
