"""Comprehensive edge-path tests for the trigger service (M27-TEST).

Covers the rejection, failure, and Postgres-update paths the focused unit tests
do not: non-numeric timestamps, audited rejections, submit-failure DLQ parking,
template/dedup errors, cron parse errors, and the Postgres store's update/delete
branches.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from hiveplane.fleet.triggers import TriggerOutcome, TriggerRunStatus, TriggerSource
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.triggers.cron import CronError, CronExpression
from hiveplane.triggers.engine import TriggerEngine
from hiveplane.triggers.ingest import (
    SignatureError,
    TimestampError,
    WebhookRequest,
    WebhookVerifier,
)
from hiveplane.triggers.limiter import TriggerLimiter
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import InMemoryTriggerStore, PostgresTriggerStore
from postgres import reset_database

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Cron parse errors
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("expression", ["5-1 * * * *", "*/x * * * *", "1,,2 * * * *"])
def test_cron_parse_errors(expression: str) -> None:
    with pytest.raises(CronError):
        CronExpression.parse(expression)


def test_cron_next_after_none_for_impossible_day() -> None:
    assert (
        CronExpression.parse("0 0 31 2 *").next_after(
            datetime(2026, 1, 1, tzinfo=UTC), max_days=365
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Ingest rejections are audited
# --------------------------------------------------------------------------- #
def test_non_numeric_timestamp_is_rejected() -> None:
    verifier = WebhookVerifier(store=InMemoryTriggerStore(), clock=lambda: _NOW)
    request = WebhookRequest(signature="sha256=x", timestamp="soon", nonce="n", body=b"")
    with pytest.raises(TimestampError):
        verifier.verify("t1", request, "secret")


def test_rejections_are_audited() -> None:
    audit = InMemoryAuditLog(clock=lambda: _NOW)
    verifier = WebhookVerifier(
        store=InMemoryTriggerStore(), clock=lambda: _NOW, audit=audit
    )
    request = WebhookRequest(
        signature="sha256=x",
        timestamp=str(int(_NOW.timestamp())),
        nonce="n",
        body=b"",
    )
    with pytest.raises(SignatureError):
        verifier.verify("t1", request, "secret")
    records = audit.records()
    assert records and records[0].action == "trigger.webhook.rejected"


# --------------------------------------------------------------------------- #
# Engine failure paths
# --------------------------------------------------------------------------- #
class _BoomSubmitter:
    def submit(self, **kwargs: Any) -> Any:
        raise RuntimeError("execution unavailable")


def _engine(submitter: Any) -> tuple[TriggerEngine, InMemoryTriggerStore]:
    store = InMemoryTriggerStore()
    limiter = TriggerLimiter(store, clock=lambda: _NOW)
    return (
        TriggerEngine(store, limiter, submitter, clock=lambda: _NOW, id_factory=lambda: "ev-1"),
        store,
    )


def test_submit_failure_is_dead_lettered() -> None:
    engine, store = _engine(_BoomSubmitter())
    spec = TriggerSpec.model_validate(
        {
            "id": "t1",
            "source": "webhook",
            "target": {"kind": "workload", "ref": "agent-1"},
        }
    )
    decision = engine.ingest(spec, {"number": 1})
    assert decision.outcome is TriggerOutcome.FAILED
    assert store.list_dlq()[0].failure_reason == "execution unavailable"


def test_dedup_key_template_error_fails() -> None:
    engine, _ = _engine(_BoomSubmitter())
    spec = TriggerSpec.model_validate(
        {
            "id": "t1",
            "source": "webhook",
            "target": {"kind": "workload", "ref": "agent-1"},
            "dedup": {"key": "{{ event.missing }}"},
        }
    )
    decision = engine.ingest(spec, {"number": 1})
    assert decision.outcome is TriggerOutcome.FAILED


# --------------------------------------------------------------------------- #
# Schema edge paths
# --------------------------------------------------------------------------- #
def test_empty_task_template_field_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {
                "id": "t1",
                "source": "webhook",
                "target": {"kind": "workload", "ref": "a"},
                "task_template": {"": "literal"},
            }
        )


# --------------------------------------------------------------------------- #
# Postgres store update/delete branches
# --------------------------------------------------------------------------- #
def test_postgres_store_update_delete_and_nonce_paths(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresTriggerStore(pg_engine)
    spec = TriggerSpec.model_validate(
        {
            "id": "t1",
            "source": "github",
            "target": {"kind": "workload", "ref": "agent-1"},
        }
    )
    store.save_trigger(spec)
    # Update branch.
    store.save_trigger(spec.model_copy(update={"enabled": False}))
    reloaded = store.get_trigger("t1")
    assert reloaded is not None and reloaded.enabled is False
    assert store.list_triggers(enabled=True) == []

    # Re-adding a run for the same (trigger, event) updates the row.
    from hiveplane.fleet.triggers import TriggerEvent, TriggerRun

    store.add_event(
        TriggerEvent(
            event_id="ev-1",
            trigger_id="t1",
            source=TriggerSource.GITHUB,
            payload={},
            received_at=_NOW,
            dedup_key="k",
            outcome=TriggerOutcome.ACCEPTED,
        )
    )
    assert store.list_events("t1", since=_NOW - timedelta(hours=1))[0].event_id == "ev-1"
    assert store.find_dedup_event("t1", "k", since=_NOW - timedelta(hours=1)) is not None

    run = TriggerRun(
        trigger_id="t1",
        event_id="ev-1",
        run_id="run-1",
        fired_at=_NOW,
        status=TriggerRunStatus.SUBMITTED,
    )
    store.add_run(run)
    store.add_run(run.model_copy(update={"status": TriggerRunStatus.FAILED}))
    assert store.list_runs("t1")[0].status is TriggerRunStatus.FAILED

    # Claim an existing nonce inside the window updates the row and returns False.
    assert store.claim_nonce("t1", "n", seen_at=_NOW, window_seconds=300) is True
    assert store.claim_nonce("t1", "n", seen_at=_NOW, window_seconds=300) is False
    assert (
        store.claim_nonce(
            "t1", "n", seen_at=_NOW + timedelta(seconds=400), window_seconds=300
        )
        is True
    )

    # Delete cascades to events, runs, and DLQ.
    assert store.delete_trigger("t1") is True
    assert store.list_events("t1") == []
    assert store.list_runs("t1") == []

    store.clear()
    assert store.list_triggers() == []
