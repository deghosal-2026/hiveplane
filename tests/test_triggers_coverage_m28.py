"""Comprehensive edge-path tests for M28 (sources, freeze, replay, preview)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from hiveplane.fleet.triggers import TriggerOutcome
from hiveplane.tenancy import DEFAULT_CONTEXT
from hiveplane.triggers.engine import DlqEntryNotFoundError, TriggerEngine
from hiveplane.triggers.freeze import (
    FreezeDrain,
    FreezeService,
    FreezeSpec,
    InMemoryFreezeStore,
    PostgresFreezeStore,
)
from hiveplane.triggers.limiter import TriggerLimiter
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.sources import AlertmanagerSource, GitHubSource
from hiveplane.triggers.store import InMemoryTriggerStore
from hiveplane.triggers.templating import TemplateError
from postgres import reset_database

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class _Submitter:
    def submit(self, **kwargs: Any) -> Any:  # pragma: no cover - unused here
        raise AssertionError("no submit expected")


def _engine(store: InMemoryTriggerStore) -> TriggerEngine:
    return TriggerEngine(
        store,
        TriggerLimiter(store, clock=lambda: _NOW),
        _Submitter(),
        clock=lambda: _NOW,
        id_factory=lambda: "ev-1",
    )


# --------------------------------------------------------------------------- #
# Freeze edge paths
# --------------------------------------------------------------------------- #
def test_freeze_requires_scope_ref() -> None:
    with pytest.raises(ValidationError):
        FreezeSpec.model_validate(
            {
                "freeze_id": "f",
                "scope": "team",
                "starts_at": _NOW,
                "declared_by": "op",
            }
        )


def test_freeze_rejects_inverted_window() -> None:
    with pytest.raises(ValidationError):
        FreezeSpec.model_validate(
            {
                "freeze_id": "f",
                "scope": "tenant",
                "starts_at": _NOW,
                "ends_at": _NOW - timedelta(minutes=1),
                "declared_by": "op",
            }
        )


def test_freeze_not_active_before_start() -> None:
    freeze = FreezeSpec.model_validate(
        {
            "freeze_id": "f",
            "scope": "tenant",
            "starts_at": _NOW + timedelta(hours=1),
            "declared_by": "op",
        }
    )
    assert freeze.active_at(_NOW) is False


def test_drain_team_scope() -> None:
    from hiveplane.core.run import Run, RunState
    from hiveplane.execution.models import InterventionAction

    class _Runs:
        def __init__(self) -> None:
            self.interventions: list[str] = []
            self.runs = [
                Run(
                    id="run-1",
                    workload_id="agent-1",
                    caller="t",
                    state=RunState.RUNNING,
                    created_at=_NOW,
                    updated_at=_NOW,
                    team_id="platform",
                ),
                Run(
                    id="run-2",
                    workload_id="agent-2",
                    caller="t",
                    state=RunState.RUNNING,
                    created_at=_NOW,
                    updated_at=_NOW,
                    team_id="ops",
                ),
            ]

        def list_runs(self, *, ctx: Any) -> list[Run]:
            return list(self.runs)

        def intervene(
            self, run_id: str, action: InterventionAction, *, actor: str, ctx: Any
        ) -> Run:
            self.interventions.append(run_id)
            return next(r for r in self.runs if r.id == run_id)

    runs = _Runs()
    service = FreezeService(InMemoryFreezeStore(), clock=lambda: _NOW)
    freeze = FreezeSpec.model_validate(
        {
            "freeze_id": "f",
            "scope": "team",
            "scope_ref": "platform",
            "starts_at": _NOW - timedelta(minutes=1),
            "declared_by": "op",
            "drain": "abort",
        }
    )
    assert service.drain(runs, freeze, ctx=DEFAULT_CONTEXT) == ["run-1"]


def test_postgres_freeze_store_update_branch(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresFreezeStore(pg_engine)
    freeze = FreezeSpec.model_validate(
        {
            "freeze_id": "f",
            "scope": "workload",
            "scope_ref": "agent-1",
            "starts_at": _NOW,
            "declared_by": "op",
        }
    )
    store.save(freeze)
    store.save(freeze.model_copy(update={"drain": FreezeDrain.ABORT}))
    loaded = store.get("f")
    assert loaded is not None and loaded.drain is FreezeDrain.ABORT
    assert store.delete("missing") is False


# --------------------------------------------------------------------------- #
# Source edge paths
# --------------------------------------------------------------------------- #
def test_github_non_object_payload_is_rejected() -> None:
    body = b"[1, 2, 3]"
    import hashlib
    import hmac

    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    spec = TriggerSpec.model_validate(
        {"id": "t", "source": "github", "target": {"kind": "workload", "ref": "a"}}
    )
    with pytest.raises(ValueError):
        GitHubSource().parse(
            spec,
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": f"sha256={signature}"},
            body=body,
            secret="secret",
        )


def test_alertmanager_requires_alerts_list() -> None:
    spec = TriggerSpec.model_validate(
        {"id": "t", "source": "alertmanager", "target": {"kind": "workload", "ref": "a"}}
    )
    with pytest.raises(ValueError):
        AlertmanagerSource().parse(
            spec, headers={}, body=json.dumps({"alerts": "nope"}).encode(), secret=None
        )


# --------------------------------------------------------------------------- #
# Engine preview / replay edge paths
# --------------------------------------------------------------------------- #
def test_preview_renders_task_and_dedup_key() -> None:
    store = InMemoryTriggerStore()
    engine = _engine(store)
    spec = TriggerSpec.model_validate(
        {
            "id": "t",
            "source": "webhook",
            "target": {"kind": "workload", "ref": "a"},
            "task_template": {"pr": "{{ event.number }}"},
            "dedup": {"key": "{{ event.number }}"},
        }
    )
    preview = engine.preview(spec, {"number": 7})
    assert preview["task"] == {"pr": 7}
    assert preview["dedup_key"] == "7"


def test_preview_rejects_overflow() -> None:
    store = InMemoryTriggerStore()
    engine = _engine(store)
    spec = TriggerSpec.model_validate(
        {
            "id": "t",
            "source": "webhook",
            "target": {"kind": "workload", "ref": "a"},
            "task_template": {"x": "{{ event.number }}"},
            "max_field_bytes": 1,
        }
    )
    with pytest.raises(TemplateError):
        engine.preview(spec, {"number": 123456})


def test_replay_when_trigger_deleted_raises() -> None:
    from hiveplane.fleet.triggers import TriggerDlqEntry

    store = InMemoryTriggerStore()
    engine = _engine(store)
    store.add_dlq(
        TriggerDlqEntry(
            entry_id="dlq-1",
            trigger_id="gone",
            event_id="ev",
            payload={},
            headers={},
            failure_reason="x",
            attempts=1,
            created_at=_NOW,
        )
    )
    with pytest.raises(DlqEntryNotFoundError):
        engine.replay_dlq("dlq-1")


def test_freeze_suppression_outcome_is_recorded() -> None:
    store = InMemoryTriggerStore()
    engine = TriggerEngine(
        store,
        TriggerLimiter(store, clock=lambda: _NOW),
        _Submitter(),
        clock=lambda: _NOW,
        id_factory=lambda: "ev-1",
        freeze_check=lambda spec, ctx: "frozen",
    )
    spec = TriggerSpec.model_validate(
        {"id": "t", "source": "webhook", "target": {"kind": "workload", "ref": "a"}}
    )
    decision = engine.ingest(spec, {})
    assert decision.outcome is TriggerOutcome.SUPPRESSED_FREEZE
