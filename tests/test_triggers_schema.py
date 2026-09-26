"""Tests for the strict trigger DSL schema (M27-01)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hiveplane.fleet.triggers import TriggerSource, TriggerTargetKind
from hiveplane.triggers.schema import (
    AdmissionRule,
    MissedSchedulePolicy,
    TriggerSpec,
)

_WEBHOOK = {
    "id": "pr-analysis",
    "source": "github",
    "target": {"kind": "workload", "ref": "repo-analysis-agent"},
    "filter": {"event": ["pull_request"], "actions": ["opened", "synchronize"]},
    "task_template": {"repo": "{{ event.repository.full_name }}", "pr": "{{ event.number }}"},
    "dedup": {"key": "{{ event.repository.full_name }}/{{ event.number }}", "window_minutes": 30},
    "cooldown_seconds": 60,
    "rate_limit": {"max_per_minute": 30, "burst": 10},
    "admission_rule": "staging-auto",
}


def test_valid_webhook_trigger_parses() -> None:
    spec = TriggerSpec.model_validate(_WEBHOOK)
    assert spec.source is TriggerSource.GITHUB
    assert spec.target.kind is TriggerTargetKind.WORKLOAD
    assert spec.target.ref == "repo-analysis-agent"
    assert spec.filter.events == ["pull_request"]
    assert spec.admission_rule is AdmissionRule.STAGING_AUTO
    assert spec.enabled is True


def test_valid_cron_trigger_requires_schedule() -> None:
    spec = TriggerSpec.model_validate(
        {
            "id": "nightly",
            "source": "cron",
            "target": {"kind": "workload", "ref": "agent-1"},
            "schedule": "0 3 * * *",
            "timezone": "America/New_York",
            "missed_schedule_policy": "catch_up",
        }
    )
    assert spec.schedule == "0 3 * * *"
    assert spec.missed_schedule_policy is MissedSchedulePolicy.CATCH_UP


def test_cron_requires_schedule() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {"id": "c", "source": "cron", "target": {"kind": "workload", "ref": "a"}}
        )


def test_non_cron_forbids_schedule() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {**_WEBHOOK, "schedule": "0 3 * * *"}
        )


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate({**_WEBHOOK, "surprise": True})


def test_invalid_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate({**_WEBHOOK, "source": "spaceship"})


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {
                "id": "c",
                "source": "cron",
                "target": {"kind": "workload", "ref": "a"},
                "schedule": "0 3 * * *",
                "timezone": "Mars/Olympus",
            }
        )


def test_invalid_cron_expression_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {
                "id": "c",
                "source": "cron",
                "target": {"kind": "workload", "ref": "a"},
                "schedule": "99 99 * * *",
            }
        )


def test_unsafe_template_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {**_WEBHOOK, "task_template": {"x": "{{ event.a + 1 }}"}}
        )


def test_rate_limit_and_dedup_bounds() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate({**_WEBHOOK, "rate_limit": {"max_per_minute": 0}})
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate({**_WEBHOOK, "dedup": {"window_minutes": 0}})
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate({**_WEBHOOK, "cooldown_seconds": -1})


def test_watch_requires_schedule() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {"id": "w", "source": "watch", "target": {"kind": "workload", "ref": "a"}}
        )
    spec = TriggerSpec.model_validate(
        {
            "id": "w",
            "source": "watch",
            "target": {"kind": "workload", "ref": "a"},
            "schedule": "*/5 * * * *",
            "max_concurrent_runs": 1,
        }
    )
    assert spec.max_concurrent_runs == 1


def test_target_ref_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {**_WEBHOOK, "target": {"kind": "workload", "ref": ""}}
        )
