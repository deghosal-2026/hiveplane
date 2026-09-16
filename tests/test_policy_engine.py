"""Tests for the policy engine."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.decision import (
    ActionClass,
    DataSensitivity,
    DecisionOutcome,
    PolicyContext,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolRef, ToolsSpec, ToolTrustLevel
from hiveplane.policy.engine import PolicyEngine, compute_blast_radius
from hiveplane.policy.models import (
    PolicyPack,
    PolicyPackMetadata,
    PolicyPackOverride,
    PolicyPackRule,
    PolicyPackRuleMatch,
    PolicyPackSpec,
)
from hiveplane.policy.packs import InMemoryPolicyPackStore


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _tools(*, allow: list[ToolRef] | None = None, deny: list[str] | None = None) -> ToolsSpec:
    return ToolsSpec(allow=allow or [], deny=deny or [])


def _context(**overrides: object) -> PolicyContext:
    data: dict[str, object] = {
        "run_id": "run-1",
        "workload": "agent-1",
        "environment": AdmissionContext.STAGING,
        "certification_status": CertificationStatus.CERTIFIED,
    }
    data.update(overrides)
    return PolicyContext.model_validate(data)


def _engine() -> PolicyEngine:
    return PolicyEngine(InMemoryPolicyPackStore(), clock=_clock)


def test_unlisted_tool_is_denied_by_default() -> None:
    decision = _engine().evaluate(_context(tool_id="t1", tools=_tools()))
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "default.deny"


def test_read_only_tool_allowed_in_staging() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(_context(tool_id="t1", tools=tools))
    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule == "manifest.allow"


def test_same_tool_gated_in_production_when_uncertified() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            environment=AdmissionContext.PRODUCTION,
            certification_status=CertificationStatus.UNCERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "certification.production"


def test_destructive_tool_escalates_when_certified() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.DESTRUCTIVE)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            tool_trust=ToolTrustLevel.DESTRUCTIVE,
            certification_status=CertificationStatus.CERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.ESCALATE
    assert decision.rule == "trust.destructive"


def test_destructive_tool_denied_in_uncertified_production() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.DESTRUCTIVE)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            tool_trust=ToolTrustLevel.DESTRUCTIVE,
            environment=AdmissionContext.PRODUCTION,
            certification_status=CertificationStatus.UNCERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.DENY


def test_sandbox_allows_listed_tools() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.DESTRUCTIVE)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            tool_trust=ToolTrustLevel.DESTRUCTIVE,
            environment=AdmissionContext.SANDBOX,
            certification_status=CertificationStatus.UNCERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule == "sandbox.allow"


def test_require_approval_escalates() -> None:
    tools = _tools(
        allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY, require_approval=True)]
    )
    decision = _engine().evaluate(_context(tool_id="t1", tools=tools))
    assert decision.outcome is DecisionOutcome.ESCALATE
    assert decision.rule == "approvals.required"


def test_action_class_approval_escalates() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            action_class=ActionClass.HIGH_SPEND,
            approval_required_for=[ActionClass.HIGH_SPEND],
        )
    )
    assert decision.outcome is DecisionOutcome.ESCALATE


def test_manifest_deny_beats_allow() -> None:
    tools = _tools(
        allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)], deny=["t1"]
    )
    decision = _engine().evaluate(_context(tool_id="t1", tools=tools))
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "manifest.deny"


def test_quarantine_denies_everything() -> None:
    decision = _engine().evaluate(
        _context(tool_id="t1", certification_status=CertificationStatus.QUARANTINED)
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "certification.quarantined"


def test_injection_blocks() -> None:
    decision = _engine().evaluate(_context(tool_id="t1", injection_detected=True))
    assert decision.outcome is DecisionOutcome.BLOCK_INJECTION
    assert decision.rule == "injection.scan"


def test_restricted_sensitivity_denies_write() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            action_class=ActionClass.PRODUCTION_WRITE,
            data_sensitivity=DataSensitivity.RESTRICTED,
        )
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "sensitivity.restricted"


def test_pack_deny_tightens() -> None:
    store = InMemoryPolicyPackStore()
    store.save(
        PolicyPack(
            metadata=PolicyPackMetadata(name="prod-deny", team="platform", version="1.0.0"),
            spec=PolicyPackSpec(
                overrides=[
                    PolicyPackOverride(
                        match=PolicyPackRuleMatch(environment=AdmissionContext.PRODUCTION),
                        rules=[PolicyPackRule(action=DecisionOutcome.DENY)],
                    )
                ]
            ),
        )
    )
    engine = PolicyEngine(store, clock=_clock)
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = engine.evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            team="platform",
            environment=AdmissionContext.PRODUCTION,
            certification_status=CertificationStatus.CERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "pack.deny"


def test_blast_radius_bounds_and_high_denies() -> None:
    blast = compute_blast_radius(
        _context(
            tool_id="t1",
            tool_trust=ToolTrustLevel.DESTRUCTIVE,
            action_class=ActionClass.PRODUCTION_WRITE,
            data_sensitivity=DataSensitivity.RESTRICTED,
            environment=AdmissionContext.PRODUCTION,
            certification_status=CertificationStatus.UNCERTIFIED,
        )
    )
    assert 0 <= blast.score <= 100
    assert blast.score >= 71


def test_run_level_context_allowed() -> None:
    decision = _engine().evaluate(_context(environment=AdmissionContext.PRODUCTION))
    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.reason


def test_decision_is_explainable() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(_context(tool_id="t1", tools=tools))
    assert decision.rule
    assert decision.reason
    assert decision.blast_radius is not None
    assert decision.certification_status is CertificationStatus.CERTIFIED
