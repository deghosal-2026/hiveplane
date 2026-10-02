"""Tests for replay, run diff, fork, and A/B replay (M60-01..M60-05)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.replay.diff import diff_runs
from hiveplane.replay.errors import ReplayNotFoundError
from hiveplane.replay.frames import build_frames
from hiveplane.replay.models import ReplayMode
from hiveplane.replay.service import ReplayService
from hiveplane.replay.store import InMemoryReplayStore
from hiveplane.tenancy import DEFAULT_CONTEXT
from test_execution_service import _service

_NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)


def _run(
    run_id: str = "run-1",
    *,
    task: dict[str, Any] | None = None,
    result: Any = None,
    cost: float = 0.10,
    state: RunState = RunState.COMPLETED,
) -> Run:
    return Run(
        id=run_id,
        workload_id="agent-1",
        caller="operator",
        state=state,
        context=AdmissionContext.PRODUCTION,
        task=task if task is not None else {"ticket": "T-1"},
        result={"ok": True} if result is None else result,
        cost_usd=cost,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
        finished_at=_NOW,
    )


def _event(
    run_id: str,
    sequence: int,
    event_type: EventType,
    *,
    detail: str | None = None,
    from_state: RunState | None = None,
    to_state: RunState | None = None,
) -> RunEvent:
    return RunEvent(
        run_id=run_id,
        sequence=sequence,
        type=event_type,
        actor="operator",
        timestamp=_NOW,
        from_state=from_state,
        to_state=to_state,
        detail=detail,
    )


def _usage(run_id: str, **overrides: Any) -> UsageReport:
    values: dict[str, Any] = {
        "run_id": run_id,
        "input_tokens": 10,
        "output_tokens": 20,
        "tool_calls": 1,
        "cost_usd": 0.03,
        "timestamp": _NOW,
        "model_identity": "m1",
        "latency_ms": 120,
    }
    values.update(overrides)
    return UsageReport(**values)


# --- M60-01 frame-by-frame replay -------------------------------------------


def test_build_frames_reconstructs_ordered_frames_with_usage() -> None:
    run = _run()
    events = [
        _event("run-1", 0, EventType.ADMISSION, detail="allowed"),
        _event("run-1", 1, EventType.STATE_CHANGE, to_state=RunState.RUNNING),
        _event("run-1", 2, EventType.TOOL_CALL, detail="search"),
        _event("run-1", 3, EventType.USAGE),
    ]
    frames = build_frames(run, events, [_usage("run-1")])

    assert frames.run_id == "run-1"
    assert frames.frame_count == 4
    assert [frame.sequence for frame in frames.frames] == [0, 1, 2, 3]
    assert frames.frames[0].event_type == "admission"
    assert frames.frames[3].usage is not None
    assert frames.frames[3].usage.model_identity == "m1"
    assert frames.side_effects is False


def test_build_frames_is_deterministic() -> None:
    run = _run()
    events = [
        _event("run-1", 0, EventType.ADMISSION),
        _event("run-1", 1, EventType.TOOL_CALL, detail="search"),
    ]
    first = build_frames(run, events, [])
    second = build_frames(run, list(events), [])
    assert first.digest == second.digest


def test_build_frames_digest_changes_when_history_changes() -> None:
    run = _run()
    base = [_event("run-1", 0, EventType.ADMISSION)]
    changed = [
        _event("run-1", 0, EventType.ADMISSION),
        _event("run-1", 1, EventType.TOOL_CALL, detail="search"),
    ]
    assert build_frames(run, base, []).digest != build_frames(run, changed, []).digest


def test_build_frames_appends_unmatched_usage() -> None:
    run = _run()
    frames = build_frames(run, [], [_usage("run-1")])
    assert frames.frame_count == 1
    assert frames.frames[0].event_type == "model_call"
    assert frames.frames[0].usage is not None


# --- M60-02 run-to-run diff --------------------------------------------------


def test_diff_runs_identifies_meaningful_differences() -> None:
    before = _run("run-1", result={"ok": True}, cost=0.10)
    after = _run("run-2", result={"ok": False}, cost=0.25)
    before_events = [_event("run-1", 0, EventType.TOOL_CALL, detail="search")]
    after_events = [
        _event("run-2", 0, EventType.TOOL_CALL, detail="search"),
        _event("run-2", 1, EventType.TOOL_CALL, detail="write"),
    ]

    diff = diff_runs(
        before, before_events, [_usage("run-1")],
        after, after_events, [_usage("run-2", cost_usd=0.05)],
    )

    assert diff.identical is False
    assert diff.result_changed is True
    assert diff.cost_delta_usd == 0.15
    assert [call["detail"] for call in diff.tool_calls_added] == ["write"]
    assert diff.tool_calls_removed == []
    assert diff.model_calls_added[0]["model_identity"] == "m1"


def test_diff_runs_reports_identical_for_equal_runs() -> None:
    run = _run("run-1")
    events = [_event("run-1", 0, EventType.ADMISSION)]
    usage = [_usage("run-1")]
    diff = diff_runs(run, events, usage, run, events, usage)
    assert diff.identical is True
    assert diff.cost_delta_usd == 0.0


# --- service: replay / fork / A/B -------------------------------------------


def _replay_service(runs: Any) -> ReplayService:
    ids = iter(["replay-1", "replay-2", "replay-3"])
    return ReplayService(
        runs,
        InMemoryReplayStore(),
        clock=lambda: _NOW,
        id_factory=lambda: next(ids),
    )


def test_replay_records_a_frame_set(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    runs, _, _, workload = _service(make_manifest)
    run = runs.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    service = _replay_service(runs)

    frames = service.replay(run.id)

    assert frames.run_id == run.id
    assert frames.frame_count >= 1
    record = service.list()[0]
    assert record.mode is ReplayMode.REPLAY
    assert record.source_run_id == run.id
    assert record.side_effects is False


def test_fork_copies_task_and_re_runs_read_only(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    runs, _, _, workload = _service(make_manifest)
    source = runs.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        task={"ticket": "T-1", "priority": "low"},
        model_identity="m1",
    )
    service = _replay_service(runs)

    result = service.fork(
        source.id,
        edits={"priority": "high"},
        actor="operator",
        ctx=DEFAULT_CONTEXT,
    )

    forked = runs.get(result.forked_run_id)
    assert forked.task == {"ticket": "T-1", "priority": "high"}
    assert forked.read_only is True
    assert forked.shadow_of == source.id
    assert result.side_effects is False
    assert service.list()[0].mode is ReplayMode.FORK


def test_fork_with_side_effects_is_not_read_only(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    runs, _, _, workload = _service(make_manifest)
    source = runs.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        task={"ticket": "T-1"},
        model_identity="m1",
    )
    service = _replay_service(runs)

    result = service.fork(source.id, actor="operator", side_effects=True)

    forked = runs.get(result.forked_run_id)
    assert forked.read_only is False
    assert forked.shadow_of is None
    assert service.get(result.replay_id).side_effects is True


def test_ab_replays_two_workloads_on_the_same_input(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    runs, _, _, workload = _service(make_manifest)
    source = runs.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        task={"ticket": "T-1"},
        model_identity="m1",
    )
    service = _replay_service(runs)

    result = service.ab(
        source.id,
        workload_a=workload,
        workload_b=workload,
        actor="operator",
    )

    run_a = runs.get(result.run_a_id)
    run_b = runs.get(result.run_b_id)
    assert run_a.task == source.task
    assert run_b.task == source.task
    assert run_a.read_only is True and run_b.read_only is True
    assert result.diff.source_run_id == run_a.id
    assert result.diff.target_run_id == run_b.id
    assert service.list()[0].mode is ReplayMode.AB


def test_diff_runs_handles_enum_list_and_none_values() -> None:
    before = _run("r1", result=[1, 2], state=RunState.QUEUED)
    after = _run("r2", result=[3], state=RunState.COMPLETED)
    after = after.model_copy(update={"failure_reason": "boom"})

    diff = diff_runs(before, [], [], after, [], [])

    fields = {delta.field for delta in diff.field_deltas}
    assert "state" in fields
    assert "result" in fields
    assert "failure_reason" in fields
    assert diff.cost_delta_usd == 0.0


def test_replay_get_missing_raises(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    runs, _, _, _ = _service(make_manifest)
    service = _replay_service(runs)
    with pytest.raises(ReplayNotFoundError):
        service.get("ghost")
