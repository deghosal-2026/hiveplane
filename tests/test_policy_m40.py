"""Tests for the context-aware policy engine, decision records, and what-if (M40)."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.core.decision import (
    ActionClass,
    DataSensitivity,
    DecisionOutcome,
    PolicyContext,
    TimeWindow,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolRef, ToolsSpec, ToolTrustLevel
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore

_NOW = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)  # Monday 10:00 UTC


def _tools(*, allow_untrusted: bool = False) -> ToolsSpec:
    return ToolsSpec(
        allow=[
            ToolRef(
                tool_id="mcp.t.destructive",
                trust_level=ToolTrustLevel.DESTRUCTIVE,
                require_approval=True,
                allow_untrusted=allow_untrusted,
            )
        ]
    )


def _context(**overrides: object) -> PolicyContext:
    data: dict[str, object] = {
        "run_id": "run-1",
        "workload": "repo-agent",
        "environment": AdmissionContext.PRODUCTION,
        "action_class": ActionClass.DESTRUCTIVE,
        "tool_id": "mcp.t.destructive",
        "tool_trust": ToolTrustLevel.DESTRUCTIVE,
        "data_sensitivity": DataSensitivity.INTERNAL,
        "certification_status": "certified",
        "tools": _tools(),
        "at": _NOW,
    }
    data.update(overrides)
    return PolicyContext.model_validate(data)


def _engine() -> PolicyEngine:
    return PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _NOW)


def test_exhausted_budget_denies_a_write() -> None:
    decision = _engine().evaluate(_context(budget_exhausted=True))

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "budget.exhausted"


def test_exhausted_budget_denies_even_a_read_only_action() -> None:
    decision = _engine().evaluate(
        _context(action_class=ActionClass.READ_ONLY, tool_trust=ToolTrustLevel.READ_ONLY)
    )
    assert decision.outcome in {DecisionOutcome.ALLOW, DecisionOutcome.ESCALATE}

    blocked = _engine().evaluate(
        _context(
            action_class=ActionClass.READ_ONLY,
            tool_trust=ToolTrustLevel.READ_ONLY,
            budget_exhausted=True,
        )
    )
    assert blocked.rule == "budget.exhausted"


def test_untrusted_taint_denies_a_destructive_tool() -> None:
    decision = _engine().evaluate(_context(taint_untrusted=True))

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "taint.block"


def test_untrusted_taint_is_allowed_when_the_tool_opts_in() -> None:
    decision = _engine().evaluate(
        _context(taint_untrusted=True, tools=_tools(allow_untrusted=True))
    )

    assert decision.rule != "taint.block"


def test_what_if_matches_the_real_decision() -> None:
    context = _context()
    engine = _engine()

    real = engine.evaluate(context)
    what_if = engine.evaluate(context, dry_run=True)

    assert what_if.outcome is real.outcome
    assert what_if.rule == real.rule
    assert real.dry_run is False
    assert what_if.dry_run is True


def test_decision_records_the_originating_rule_and_time() -> None:
    decision = _engine().evaluate(_context())

    assert decision.rule == "certification.production" or decision.rule.startswith("trust")
    assert decision.timestamp == _NOW


def test_policy_pack_decision_records_the_pack_version() -> None:
    from hiveplane.policy.models import (
        PolicyPack,
        PolicyPackMetadata,
        PolicyPackOverride,
        PolicyPackRule,
        PolicyPackRuleMatch,
        PolicyPackSpec,
    )

    packs = InMemoryPolicyPackStore()
    packs.save(
        PolicyPack(
            metadata=PolicyPackMetadata(name="strict", team="platform", version="3"),
            spec=PolicyPackSpec(
                overrides=[
                    PolicyPackOverride(
                        match=PolicyPackRuleMatch(environment=AdmissionContext.PRODUCTION),
                        rules=[
                            PolicyPackRule(
                                action=DecisionOutcome.DENY,
                                action_class=ActionClass.DESTRUCTIVE,
                            )
                        ],
                    )
                ]
            ),
        )
    )
    decision = PolicyEngine(packs, clock=lambda: _NOW).evaluate(_context(team="platform"))

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "pack.deny"
    assert decision.pack_version == "3"


def test_time_window_denies_outside_business_hours() -> None:
    window = TimeWindow(
        action_class=ActionClass.DESTRUCTIVE,
        days=[0, 1, 2, 3, 4],
        start="09:00",
        end="17:00",
        tz="UTC",
    )
    sunday = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
    decision = _engine().evaluate(_context(time_windows=[window], at=sunday))

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "outside_time_window"


def test_time_window_allows_inside_business_hours() -> None:
    window = TimeWindow(
        action_class=ActionClass.DESTRUCTIVE,
        days=[0, 1, 2, 3, 4],
        start="09:00",
        end="17:00",
        tz="UTC",
    )
    decision = _engine().evaluate(_context(time_windows=[window]))

    assert decision.rule != "outside_time_window"


def test_blackout_window_denies_matched_actions() -> None:
    blackout = TimeWindow(blackout=True)
    decision = _engine().evaluate(_context(time_windows=[blackout]))

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "blackout"
