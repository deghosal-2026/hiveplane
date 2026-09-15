"""Tests for trigger, tool, sandbox, shaping, fan-out, and health models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hiveplane.core.fanout import FanOutDestination, FanOutSpec, FanOutType
from hiveplane.core.health import HealthSpec, ReadinessProbe, Slo
from hiveplane.core.sandbox import EgressSpec, ResourceCaps, SandboxSpec
from hiveplane.core.shaping import FilterRule, OutputShapingSpec, TruncateStrategy
from hiveplane.core.tools import McpServer, ToolRef, ToolsSpec, ToolTrustLevel
from hiveplane.core.triggers import TriggerMode, TriggerRule, TriggerType


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def test_tools_deny_wins_over_allow() -> None:
    spec = ToolsSpec(
        allow=[ToolRef(tool_id="github.create_pr", trust_level=ToolTrustLevel.DESTRUCTIVE)],
        deny=["github.create_pr"],
    )

    assert spec.is_denied("github.create_pr") is True
    assert spec.is_allowed("github.create_pr") is False


def test_tools_default_trust_is_read_only() -> None:
    spec = ToolsSpec()

    assert spec.default_trust is ToolTrustLevel.READ_ONLY
    assert spec.is_allowed("anything") is False


def test_tool_ref_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ToolRef.model_validate({"tool_id": "x", "trust_level": "read_only", "bogus": True})


def test_tool_id_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        ToolRef(tool_id="", trust_level=ToolTrustLevel.READ_ONLY)


def test_mcp_server_round_trips() -> None:
    server = McpServer(name="github-mcp", endpoint="http://mcp-fabric/github")

    assert server.name == "github-mcp"


def test_tools_approval_required_for_destructive() -> None:
    spec = ToolsSpec(
        allow=[
            ToolRef(
                tool_id="pagerduty.acknowledge",
                trust_level=ToolTrustLevel.DESTRUCTIVE,
                require_approval=True,
            )
        ]
    )

    assert spec.approval_required("pagerduty.acknowledge") is True
    assert spec.approval_required("other") is False


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #
def test_webhook_trigger_requires_url_and_match() -> None:
    trigger = TriggerRule.model_validate(
        {"type": "webhook", "url": "/hooks/incident", "match": {"severity": ["critical"]}}
    )

    assert trigger.type is TriggerType.WEBHOOK


def test_webhook_trigger_without_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="url"):
        TriggerRule.model_validate({"type": "webhook", "match": {"severity": ["critical"]}})


def test_alert_trigger_requires_source() -> None:
    with pytest.raises(ValidationError, match="source"):
        TriggerRule.model_validate({"type": "alert", "match": {"service": "payments"}})


def test_github_pr_trigger_requires_events() -> None:
    with pytest.raises(ValidationError, match="events"):
        TriggerRule.model_validate({"type": "github_pr", "match": {"paths": ["src/**"]}})


def test_cron_trigger_requires_schedule() -> None:
    with pytest.raises(ValidationError, match="schedule"):
        TriggerRule(type=TriggerType.CRON)


def test_cron_trigger_with_schedule_and_mode() -> None:
    trigger = TriggerRule(
        type=TriggerType.CRON,
        schedule="0 */6 * * *",
        mode=TriggerMode.WATCH,
        max_concurrent=1,
    )

    assert trigger.mode is TriggerMode.WATCH


def test_non_cron_trigger_requires_a_match_criterion() -> None:
    with pytest.raises(ValidationError, match="match"):
        TriggerRule(type=TriggerType.WEBHOOK, url="/hooks/x")


# --------------------------------------------------------------------------- #
# Sandbox
# --------------------------------------------------------------------------- #
def test_sandbox_requires_resource_caps_when_enabled() -> None:
    with pytest.raises(ValidationError, match="resource_caps"):
        SandboxSpec(enabled=True)


def test_sandbox_denies_metadata_endpoint_by_default() -> None:
    spec = SandboxSpec(
        enabled=True,
        resource_caps=ResourceCaps(memory_mb=512, cpu_cores=1.0, wall_clock_s=300),
    )

    assert "169.254.169.254" in spec.egress.deny


def test_resource_caps_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ResourceCaps(memory_mb=0, cpu_cores=1.0, wall_clock_s=300)


def test_egress_mode_defaults_to_restricted() -> None:
    assert EgressSpec().mode.value == "restricted"


# --------------------------------------------------------------------------- #
# Output shaping
# --------------------------------------------------------------------------- #
def test_output_shaping_max_bytes_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        OutputShapingSpec(max_bytes=0)


def test_filter_rule_pattern_must_compile() -> None:
    with pytest.raises(ValidationError, match="regex"):
        FilterRule.model_validate({"pattern": "([", "action": "redact"})


def test_output_shaping_defaults() -> None:
    spec = OutputShapingSpec(max_bytes=1024)

    assert spec.truncate_strategy is TruncateStrategy.HEAD
    assert spec.injection_scan is True


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #
def test_slack_destination_requires_channel() -> None:
    with pytest.raises(ValidationError, match="channel"):
        FanOutDestination(type=FanOutType.SLACK)


def test_jira_destination_requires_project_and_issue_type() -> None:
    with pytest.raises(ValidationError, match="project"):
        FanOutDestination(type=FanOutType.JIRA, issue_type="Incident")


def test_webhook_destination_requires_url() -> None:
    with pytest.raises(ValidationError, match="url"):
        FanOutDestination(type=FanOutType.WEBHOOK)


def test_fanout_spec_groups_by_terminal_state() -> None:
    spec = FanOutSpec(
        on_completed=[FanOutDestination(type=FanOutType.SLACK, channel="#results")],
        on_failed=[FanOutDestination(type=FanOutType.JIRA, project="PLAT", issue_type="Task")],
    )

    assert len(spec.on_completed) == 1
    assert spec.on_escalation == []


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health_slo_targets_must_be_within_unit_interval() -> None:
    with pytest.raises(ValidationError):
        Slo(availability_target=1.5, quality_target=0.9, error_budget_window=3600)


def test_failure_rate_threshold_range() -> None:
    with pytest.raises(ValidationError):
        HealthSpec(failure_rate_threshold=2.0)


def test_health_defaults() -> None:
    health = HealthSpec()

    assert isinstance(health.readiness_probe, ReadinessProbe)
    assert health.slo.availability_target == 0.99
