"""The replay service: frame replay, run diff, fork, and A/B replay (M60).

Every operation is side-effect-free by default. A fork or A/B replay submits its
new runs in the isolated ``sandbox`` context with ``read_only=True`` and
``shadow_of`` set, so destructive tools are blocked and nothing is delivered to
fan-out. Passing ``side_effects=True`` clears both guards and is recorded on the
replay for audit.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from pydantic import JsonValue

from hiveplane.core.event import RunEvent
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.execution.service import RunService
from hiveplane.replay.diff import diff_runs
from hiveplane.replay.errors import ReplayNotFoundError
from hiveplane.replay.frames import build_frames
from hiveplane.replay.models import (
    ABReplayResult,
    ForkResult,
    ReplayFrameSet,
    ReplayMode,
    ReplayRecord,
    RunDiff,
)
from hiveplane.replay.store import ReplayStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import context_for_run


class ReplayService:
    """Reconstructs, diffs, forks, and A/B-replays runs."""

    def __init__(
        self,
        runs: RunService,
        store: ReplayStore,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._runs = runs
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"replay-{uuid.uuid4().hex[:12]}")

    def replay(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ReplayFrameSet:
        """Reconstruct a run frame-by-frame and record the replay (no side effects)."""
        run = self._runs.get(run_id, ctx=ctx)
        events = _sorted_events(self._runs.events(run_id, ctx=ctx))
        frames = build_frames(run, events, self._runs.usage(run_id, ctx=ctx))
        self._record(
            run,
            mode=ReplayMode.REPLAY,
            actor="operator",
            side_effects=False,
        )
        return frames

    def diff(
        self,
        before_run_id: str,
        after_run_id: str,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> RunDiff:
        """Compare two runs by state, calls, cost, and outcome."""
        before = self._runs.get(before_run_id, ctx=ctx)
        after = self._runs.get(after_run_id, ctx=ctx)
        return diff_runs(
            before,
            _sorted_events(self._runs.events(before_run_id, ctx=ctx)),
            self._runs.usage(before_run_id, ctx=ctx),
            after,
            _sorted_events(self._runs.events(after_run_id, ctx=ctx)),
            self._runs.usage(after_run_id, ctx=ctx),
        )

    def fork(
        self,
        source_run_id: str,
        *,
        edits: Mapping[str, JsonValue] | None = None,
        fork_point: int | None = None,
        side_effects: bool = False,
        actor: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ForkResult:
        """Copy a run's task (with edits) into a new run and re-run it."""
        source = self._runs.get(source_run_id, ctx=ctx)
        run_ctx = _run_ctx(source)
        task: dict[str, JsonValue] = {**source.task, **(dict(edits) if edits else {})}
        forked = self._submit(
            source,
            workload=source.workload_id,
            task=task,
            side_effects=side_effects,
            ctx=run_ctx,
        )
        record = self._record(
            source,
            mode=ReplayMode.FORK,
            actor=actor,
            side_effects=side_effects,
            fork_point=fork_point,
            run_ids=[forked.id],
        )
        return ForkResult(
            replay_id=record.replay_id,
            source_run_id=source.id,
            forked_run_id=forked.id,
            task=task,
            fork_point=fork_point,
            side_effects=side_effects,
        )

    def ab(
        self,
        source_run_id: str,
        *,
        workload_a: str,
        workload_b: str,
        side_effects: bool = False,
        actor: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ABReplayResult:
        """Run two workloads on identical input and diff the two arms."""
        source = self._runs.get(source_run_id, ctx=ctx)
        run_ctx = _run_ctx(source)
        run_a = self._submit(
            source, workload=workload_a, task=source.task, side_effects=side_effects, ctx=run_ctx
        )
        run_b = self._submit(
            source, workload=workload_b, task=source.task, side_effects=side_effects, ctx=run_ctx
        )
        diff = self.diff(run_a.id, run_b.id, ctx=run_ctx)
        record = self._record(
            source,
            mode=ReplayMode.AB,
            actor=actor,
            side_effects=side_effects,
            run_ids=[run_a.id, run_b.id],
            detail=f"{workload_a} vs {workload_b}",
        )
        return ABReplayResult(
            replay_id=record.replay_id,
            source_run_id=source.id,
            run_a_id=run_a.id,
            run_b_id=run_b.id,
            workload_a=workload_a,
            workload_b=workload_b,
            side_effects=side_effects,
            diff=diff,
        )

    def list(
        self,
        *,
        source_run_id: str | None = None,
        mode: ReplayMode | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ReplayRecord]:
        """List recorded replay operations."""
        return self._store.list_replays(source_run_id=source_run_id, mode=mode, ctx=ctx)

    def get(
        self, replay_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ReplayRecord:
        """Return a replay record or raise when absent/out of scope."""
        record = self._store.get_replay(replay_id, ctx=ctx)
        if record is None:
            raise ReplayNotFoundError(replay_id)
        return record

    def _submit(
        self,
        source: Run,
        *,
        workload: str,
        task: dict[str, JsonValue],
        side_effects: bool,
        ctx: TenantContext,
    ) -> Run:
        run = self._runs.submit(
            workload=workload,
            caller="replay",
            context=AdmissionContext.SANDBOX,
            task=task,
            model_identity=source.model_identity,
            read_only=not side_effects,
            shadow_of=None if side_effects else source.id,
            ctx=ctx,
        )
        with contextlib.suppress(Exception):
            self._runs.start(run.id, actor="replay", ctx=ctx)
        return self._runs.get(run.id, ctx=ctx)
    def _record(
        self,
        source: Run,
        *,
        mode: ReplayMode,
        actor: str,
        side_effects: bool,
        fork_point: int | None = None,
        run_ids: Sequence[str] | None = None,
        detail: str | None = None,
    ) -> ReplayRecord:
        record = ReplayRecord(
            replay_id=self._id_factory(),
            source_run_id=source.id,
            mode=mode,
            actor=actor,
            fork_point=fork_point,
            side_effects=side_effects,
            run_ids=list(run_ids) if run_ids else [],
            detail=detail,
            created_at=self._clock(),
            tenant_id=source.tenant_id,
        )
        self._store.add_replay(record, ctx=_run_ctx(source))
        return record


def _run_ctx(run: Run) -> TenantContext:
    return context_for_run(run.tenant_id, run.team_id, run.attribution_key)


def _sorted_events(events: list[RunEvent]) -> list[RunEvent]:
    return sorted(events, key=lambda event: event.sequence)
