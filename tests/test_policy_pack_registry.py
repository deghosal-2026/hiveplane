"""Tests for the team policy pack registry: lint, publish, resolve, apply (M40-04)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.decision import ActionClass, DecisionOutcome
from hiveplane.core.run import AdmissionContext
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.errors import PolicyPackAlreadyExistsError
from hiveplane.policy.models import (
    PolicyPack,
    PolicyPackMetadata,
    PolicyPackOverride,
    PolicyPackRule,
    PolicyPackRuleMatch,
    PolicyPackSpec,
)
from hiveplane.policy.pack_registry import PolicyPackRegistry
from hiveplane.policy.packs import InMemoryPolicyPackStore

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _pack(
    name: str = "strict",
    *,
    version: str = "1",
    team: str = "platform",
    inherits: list[str] | None = None,
    deny_destructive: bool = True,
) -> PolicyPack:
    overrides = (
        [
            PolicyPackOverride(
                match=PolicyPackRuleMatch(environment=AdmissionContext.PRODUCTION),
                rules=[
                    PolicyPackRule(
                        action=DecisionOutcome.DENY, action_class=ActionClass.DESTRUCTIVE
                    )
                ],
            )
        ]
        if deny_destructive
        else []
    )
    return PolicyPack(
        metadata=PolicyPackMetadata(name=name, team=team, version=version),
        spec=PolicyPackSpec(overrides=overrides, inherits=inherits or []),
    )


def _registry() -> tuple[PolicyPackRegistry, InMemoryPolicyPackStore]:
    store = InMemoryPolicyPackStore()
    return PolicyPackRegistry(store, clock=lambda: _NOW), store


def test_lint_accepts_a_valid_pack() -> None:
    registry, _ = _registry()

    result = registry.lint(_pack())

    assert result.valid is True
    assert result.issues == []


def test_lint_flags_an_unknown_parent() -> None:
    registry, _ = _registry()

    result = registry.lint(_pack(inherits=["missing"]), available={})

    assert result.valid is False
    assert "inherits unknown pack 'missing'" in result.issues


def test_lint_detects_an_inheritance_cycle() -> None:
    registry, _ = _registry()
    parent = _pack("parent", inherits=["child"])
    child = _pack("child", inherits=["parent"])

    result = registry.lint(parent, available={"parent": parent, "child": child})

    assert result.valid is False
    assert "inheritance cycle detected" in result.issues


def test_publish_is_immutable_by_name() -> None:
    registry, _ = _registry()
    registry.publish(_pack())

    with pytest.raises(PolicyPackAlreadyExistsError):
        registry.publish(_pack(version="2"))


def test_resolve_returns_parents_first() -> None:
    registry, _ = _registry()
    parent = _pack("parent")
    child = _pack("child", inherits=["parent"])

    chain = registry.resolve(child, available={"parent": parent, "child": child})

    assert [pack.metadata.name for pack in chain] == ["parent", "child"]


def test_apply_pins_the_resolved_chain_to_a_team() -> None:
    registry, store = _registry()
    registry.publish(_pack("parent"))
    registry.publish(_pack("child", inherits=["parent"]))

    applied = registry.apply("child", team="payments")

    assert [pack.metadata.team for pack in applied] == ["payments", "payments"]
    assert {pack.metadata.name for pack in store.for_team("payments")} == {
        "payments:parent",
        "payments:child",
    }


def test_applied_pack_denies_via_the_engine() -> None:
    from hiveplane.core.decision import PolicyContext
    from hiveplane.core.tools import ToolRef, ToolsSpec, ToolTrustLevel

    registry, store = _registry()
    registry.publish(_pack("child"))
    registry.apply("child", team="payments")
    engine = PolicyEngine(store, clock=lambda: _NOW)
    context = PolicyContext(
        run_id="run-1",
        workload="repo-agent",
        team="payments",
        environment=AdmissionContext.PRODUCTION,
        action_class=ActionClass.DESTRUCTIVE,
        tool_id="mcp.t.destructive",
        tool_trust=ToolTrustLevel.DESTRUCTIVE,
        certification_status=CertificationStatus.CERTIFIED,
        tools=ToolsSpec(
            allow=[
                ToolRef(
                    tool_id="mcp.t.destructive",
                    trust_level=ToolTrustLevel.DESTRUCTIVE,
                    require_approval=True,
                )
            ]
        ),
    )

    decision = engine.evaluate(context)

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "pack.deny"
