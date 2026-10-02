"""Tests for the replay store (in-memory and Postgres-gated) (M60)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.replay.models import ReplayMode, ReplayRecord
from hiveplane.replay.store import (
    InMemoryReplayStore,
    PostgresReplayStore,
    build_replay_store,
)
from hiveplane.tenancy import SYSTEM_CONTEXT, TenantContext, TenantScopeError
from postgres import ensure_schema, reset_database

_NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)
_OTHER = TenantContext(tenant_id="other")


def _record(replay_id: str = "replay-1", *, tenant: str = "default") -> ReplayRecord:
    return ReplayRecord(
        replay_id=replay_id,
        source_run_id="run-1",
        mode=ReplayMode.FORK,
        actor="operator",
        side_effects=False,
        run_ids=["run-2"],
        created_at=_NOW,
        tenant_id=tenant,
    )


def test_in_memory_store_round_trips() -> None:
    store = InMemoryReplayStore()
    store.add_replay(_record())
    assert store.get_replay("replay-1") == _record()
    assert [record.replay_id for record in store.list_replays()] == ["replay-1"]
    assert [record.replay_id for record in store.list_replays(mode=ReplayMode.FORK)] == [
        "replay-1"
    ]
    assert store.list_replays(mode=ReplayMode.AB) == []


def test_in_memory_store_scopes_by_tenant() -> None:
    store = InMemoryReplayStore()
    store.add_replay(_record(tenant="other"), ctx=_OTHER)
    assert store.get_replay("replay-1", ctx=_OTHER) is not None
    assert store.get_replay("replay-1") is None
    assert store.list_replays() == []


def test_build_replay_store_defaults_to_memory() -> None:
    assert isinstance(build_replay_store(), InMemoryReplayStore)


def test_postgres_store_round_trips(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    ensure_schema(pg_engine)
    store = PostgresReplayStore(pg_engine)

    store.add_replay(_record())
    loaded = store.get_replay("replay-1")
    assert loaded is not None
    assert loaded.mode is ReplayMode.FORK
    assert [record.replay_id for record in store.list_replays()] == ["replay-1"]
    assert store.get_replay("replay-1", ctx=_OTHER) is None

    store.add_replay(_record())
    assert len(store.list_replays()) == 1

    store.clear()
    assert store.list_replays() == []


def test_postgres_store_rejects_cross_tenant_write(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    ensure_schema(pg_engine)
    store = PostgresReplayStore(pg_engine)

    with pytest.raises(TenantScopeError):
        store.add_replay(_record(tenant="other"), ctx=TenantContext(tenant_id="default"))


def test_in_memory_store_clear() -> None:
    store = InMemoryReplayStore()
    store.add_replay(_record())
    store.clear()
    assert store.list_replays() == []


def test_postgres_store_filters_and_system_scope(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresReplayStore(pg_engine)
    store.add_replay(_record("replay-1"))
    store.add_replay(_record("replay-2"))
    store.add_replay(
        ReplayRecord(
            replay_id="replay-3",
            source_run_id="run-9",
            mode=ReplayMode.AB,
            actor="operator",
            run_ids=["run-a", "run-b"],
            created_at=_NOW,
            tenant_id="other",
        ),
        ctx=_OTHER,
    )

    assert len(store.list_replays(source_run_id="run-1")) == 2
    assert [r.replay_id for r in store.list_replays(mode=ReplayMode.AB, ctx=_OTHER)] == [
        "replay-3"
    ]
    assert len(store.list_replays(ctx=SYSTEM_CONTEXT)) == 3
    assert store.get_replay("ghost") is None
