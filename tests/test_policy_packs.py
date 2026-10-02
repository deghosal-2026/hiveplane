"""Tests for policy pack models and the pack store (M58-03)."""

from __future__ import annotations

import pytest

from hiveplane.core.decision import DataSensitivity, DecisionOutcome
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.policy.errors import PolicyPackAlreadyExistsError
from hiveplane.policy.models import (
    PolicyPack,
    PolicyPackDefaults,
    PolicyPackMetadata,
    PolicyPackOverride,
    PolicyPackRule,
    PolicyPackRuleMatch,
    PolicyPackSpec,
)
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.tenancy import Role, TenantContext

_A = TenantContext(tenant_id="tenant-a", role=Role.ADMIN)
_B = TenantContext(tenant_id="tenant-b", role=Role.ADMIN)


def _pack(
    name: str = "platform-default", team: str = "platform", *, tenant_id: str = "default"
) -> PolicyPack:
    return PolicyPack(
        apiVersion="hiveplane/v1",
        kind="PolicyPack",
        metadata=PolicyPackMetadata(
            name=name, team=team, version="1.0.0", tenant_id=tenant_id
        ),
        spec=PolicyPackSpec(
            defaults=PolicyPackDefaults(),
            overrides=[
                PolicyPackOverride(
                    match=PolicyPackRuleMatch(
                        environment=AdmissionContext.PRODUCTION,
                        data_sensitivity=DataSensitivity.PII,
                    ),
                    rules=[
                        PolicyPackRule(
                            action=DecisionOutcome.DENY, tool_trust=ToolTrustLevel.DESTRUCTIVE
                        )
                    ],
                )
            ],
        ),
    )


def test_pack_round_trips() -> None:
    pack = _pack()
    assert pack.metadata.team == "platform"
    assert pack.spec.overrides[0].rules[0].action is DecisionOutcome.DENY


def test_store_save_get_list_for_team() -> None:
    store = InMemoryPolicyPackStore()
    store.save(_pack("a", "platform"))
    store.save(_pack("b", "other"))
    assert store.get("a") is not None
    assert {p.metadata.name for p in store.list_packs()} == {"a", "b"}
    assert {p.metadata.name for p in store.for_team("platform")} == {"a"}
    assert {p.metadata.name for p in store.for_team(None)} == set()


def test_store_rejects_duplicate() -> None:
    store = InMemoryPolicyPackStore()
    store.save(_pack("a"))
    with pytest.raises(PolicyPackAlreadyExistsError):
        store.save(_pack("a"))


def test_two_tenants_may_own_the_same_pack_name() -> None:
    store = InMemoryPolicyPackStore()
    store.save(_pack("platform-pack", tenant_id="tenant-a"), ctx=_A)
    store.save(_pack("platform-pack", tenant_id="tenant-b"), ctx=_B)

    assert store.get("platform-pack", ctx=_A) is not None
    assert store.get("platform-pack", ctx=_B) is not None
    assert {pack.metadata.tenant_id for pack in store.list_packs(ctx=_A)} == {"tenant-a"}
    assert {pack.metadata.tenant_id for pack in store.list_packs(ctx=_B)} == {"tenant-b"}


def test_duplicate_check_is_per_tenant() -> None:
    store = InMemoryPolicyPackStore()
    store.save(_pack("platform-pack", tenant_id="tenant-a"), ctx=_A)
    with pytest.raises(PolicyPackAlreadyExistsError):
        store.save(_pack("platform-pack", tenant_id="tenant-a"), ctx=_A)
    store.save(_pack("platform-pack", tenant_id="tenant-b"), ctx=_B)
