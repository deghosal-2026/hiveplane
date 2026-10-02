"""Regression tests for the M61 execution/runtime code-review fixes.

Issues #498-#508: tenant context at the tool/adapter boundary, admission-paused
resume, approval scoping, sandbox re-provisioning on re-drive, channel token
lifecycle, subprocess environment isolation, event-append sequencing, guard
state bounds, checkpoint file growth, and per-workload guard limits.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.api.app import create_app
from hiveplane.api.deps import TENANT_HEADER
from hiveplane.api.sandbox_channel import SandboxChannel
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.approval import ApprovalStatus
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord, InterventionAction, RunContext
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.subprocess_spawner import SpawnOutcome
from hiveplane.execution.tools import ToolCallOutcome, ToolCallRequest, ToolGateway
from hiveplane.execution.wiring import (
    attach_raw_worker,
    build_guard_limit_lookup,
    build_tool_gateway,
)
from hiveplane.guards.context import ContextBudgetGuard
from hiveplane.guards.manager import GuardLimits, GuardManager
from hiveplane.guards.velocity import SpendVelocityGuard
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.registry.models import ToolRecord
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.tenancy import Role
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext, context_for_run
from test_execution_admission import _Cert, _Policy, _Sandbox

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_MODEL = "openai/gpt-4o/2024-08-06"
_ACME = TenantContext(tenant_id="acme", role=Role.ADMIN)
_BETA = TenantContext(tenant_id="beta", role=Role.ADMIN)


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


class _GatewayRuns:
    """Minimal RunAccess double for gateway-level tests."""

    def __init__(self, run: Run) -> None:
        self._run = run

    def get(self, run_id: str, *, ctx: object = None) -> Run:
        return self._run

    def intervene(
        self,
        run_id: str,
        action: InterventionAction,
        *,
        actor: str,
        ctx: object = None,
    ) -> Run:
        return self._run

    def record_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        detail: str | None = None,
        ctx: object = None,
    ) -> None:
        return None


def _gateway(
    workload: AgentWorkload, run: Run, approvals: ApprovalService
) -> ToolGateway:
    registry = SimpleNamespace(get=lambda name, **_: SimpleNamespace(manifest=workload))
    return ToolGateway(
        registry,  # type: ignore[arg-type]
        _Policy(DecisionOutcome.ESCALATE),
        _GatewayRuns(run),
        approvals=approvals,
        clock=lambda: _NOW,
    )


def _tenant_harness(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    entrypoint: str,
    ctx: TenantContext,
    tool_id: str = "mcp.t.read",
    tool_trust: str = "read_only",
    admission: DecisionOutcome = DecisionOutcome.ALLOW,
) -> tuple[RunService, RegistryService, ApprovalService]:
    tools = tmp_path / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / f"{tool_id}.json").write_text('{"status": "ok"}', encoding="utf-8")
    monkeypatch.setenv("HIVEPLANE_EXECUTION__TOOL_FIXTURES", str(tools))
    registry = RegistryService(InMemoryRegistryStore())
    registry.register_tool(
        ToolRecord(
            tool_id=tool_id,
            name=tool_id,
            mcp_server="test",
            trust_level=ToolTrustLevel(tool_trust),
            registered_at=_NOW,
            registered_by="test",
        ),
        ctx=ctx,
    )
    workload = make_manifest(
        name="agent-1",
        runtime={"adapter": "raw-worker", "entrypoint": entrypoint},
        tools={"allow": [{"tool_id": tool_id, "trust_level": tool_trust}]},
    )
    registry.create(workload, ctx=ctx)
    approvals = ApprovalService(InMemoryApprovalStore())
    budget = BudgetService(InMemoryBudgetStore(), CostTable())
    service = RunService(
        InMemoryRunStore(),
        registry,
        admission=AdmissionPipeline(
            _Cert(True), _Policy(admission), budget, _Sandbox(True)
        ),
        executor=None,
        fanout=_FanOut(),
        approvals=approvals,
        budget=budget,
    )
    return service, registry, approvals


def _write_worker(tmp_path: Path, source: str, *, name: str = "worker") -> str:
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    return f"{name}:run"


def test_tenant_run_executes_tool_calls_end_to_end(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entrypoint = _write_worker(
        tmp_path,
        "def run(task, ctx):\n"
        "    ctx.tool_call('mcp.t.read')\n"
        "    return {'ok': True}\n",
    )
    service, registry, approvals = _tenant_harness(
        make_manifest, monkeypatch, tmp_path, entrypoint=entrypoint, ctx=_ACME
    )
    gateway = build_tool_gateway(
        registry, _Policy(DecisionOutcome.ALLOW), service, approvals
    )
    attach_raw_worker(service, gateway, root=tmp_path, spawner=lambda work: work())

    run = service.submit(
        workload="agent-1",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity=_MODEL,
        ctx=_ACME,
    )
    service.start(run.id, actor="cli", ctx=_ACME)

    completed = service.get(run.id, ctx=_ACME)
    assert completed.state is RunState.COMPLETED
    assert completed.result == {"ok": True}
    assert completed.tenant_id == "acme"


def test_admission_escalated_run_executes_after_approval(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entrypoint = _write_worker(
        tmp_path,
        "def run(task, ctx):\n"
        "    return {'ok': True}\n",
    )
    service, registry, approvals = _tenant_harness(
        make_manifest,
        monkeypatch,
        tmp_path,
        entrypoint=entrypoint,
        ctx=_ACME,
        admission=DecisionOutcome.ESCALATE,
    )
    gateway = build_tool_gateway(
        registry, _Policy(DecisionOutcome.ALLOW), service, approvals
    )
    attach_raw_worker(service, gateway, root=tmp_path, spawner=lambda work: work())

    run = service.submit(
        workload="agent-1",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity=_MODEL,
        ctx=_ACME,
    )
    assert service.get(run.id, ctx=_ACME).state is RunState.PAUSED
    assert service.get(run.id, ctx=_ACME).started_at is None

    pending = approvals.list(run_id=run.id, ctx=_ACME)
    approvals.decide(
        pending[0].approval_id,
        status=ApprovalStatus.APPROVED,
        operator="op",
        ctx=_ACME,
    )
    service.intervene(run.id, InterventionAction.RESUME, actor="op", ctx=_ACME)

    completed = service.get(run.id, ctx=_ACME)
    assert completed.state is RunState.COMPLETED
    assert completed.result == {"ok": True}


def test_gated_trigger_run_executes_after_approval(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entrypoint = _write_worker(
        tmp_path,
        "def run(task, ctx):\n"
        "    return {'ok': True}\n",
    )
    service, registry, approvals = _tenant_harness(
        make_manifest, monkeypatch, tmp_path, entrypoint=entrypoint, ctx=_ACME
    )
    gateway = build_tool_gateway(
        registry, _Policy(DecisionOutcome.ALLOW), service, approvals
    )
    attach_raw_worker(service, gateway, root=tmp_path, spawner=lambda work: work())

    run = service.submit(
        workload="agent-1",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity=_MODEL,
        require_approval=True,
        ctx=_ACME,
    )
    assert service.get(run.id, ctx=_ACME).state is RunState.PAUSED

    pending = approvals.list(run_id=run.id, ctx=_ACME)
    approvals.decide(
        pending[0].approval_id,
        status=ApprovalStatus.APPROVED,
        operator="op",
        ctx=_ACME,
    )
    service.intervene(run.id, InterventionAction.RESUME, actor="op", ctx=_ACME)

    assert service.get(run.id, ctx=_ACME).state is RunState.COMPLETED


def test_tool_call_endpoint_rejects_cross_tenant_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=context_for_run("default"))
    app.state.run_service.store.save_run(
        Run(
            id="run-x",
            workload_id="agent-a",
            caller="cli",
            state=RunState.RUNNING,
            context=AdmissionContext.SANDBOX,
            created_at=_NOW,
            updated_at=_NOW,
            tenant_id="default",
        ),
        ctx=context_for_run("default"),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/runs/run-x/tool-calls",
        headers={TENANT_HEADER: "beta"},
        json={"tool_id": "mcp.t.read"},
    )

    assert response.status_code == 404


def _two_destructive_workload(
    make_manifest: Callable[..., AgentWorkload],
) -> AgentWorkload:
    return make_manifest(
        name="agent-1",
        tools={
            "allow": [
                {"tool_id": "mcp.t.drop", "trust_level": "destructive"},
                {"tool_id": "mcp.t.delete", "trust_level": "destructive"},
            ]
        },
    )


def _running_run() -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.RUNNING,
        context=AdmissionContext.SANDBOX,
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_approval_does_not_authorize_different_tool_same_rule(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    workload = _two_destructive_workload(make_manifest)
    approvals = ApprovalService(InMemoryApprovalStore(), clock=lambda: _NOW)
    gateway = _gateway(workload, _running_run(), approvals)

    first = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.drop"))
    assert first.outcome is ToolCallOutcome.ESCALATED
    assert first.approval_id is not None
    approvals.decide(first.approval_id, status=ApprovalStatus.APPROVED, operator="op")

    second = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.delete"))

    assert second.outcome is ToolCallOutcome.ESCALATED
    assert second.approval_id != first.approval_id


def test_approval_is_consumed_after_one_authorized_call(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    workload = _two_destructive_workload(make_manifest)
    approvals = ApprovalService(InMemoryApprovalStore(), clock=lambda: _NOW)
    gateway = _gateway(workload, _running_run(), approvals)

    first = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.drop"))
    assert first.approval_id is not None
    approvals.decide(first.approval_id, status=ApprovalStatus.APPROVED, operator="op")

    allowed = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.drop"))
    assert allowed.outcome is ToolCallOutcome.ALLOWED

    again = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.drop"))
    assert again.outcome is ToolCallOutcome.ESCALATED


class _RecordingSpawner:
    """A SubprocessSpawner double that records each launch."""

    def __init__(self) -> None:
        self.launched: list[list[str]] = []
        self._cv = threading.Condition()

    def launch(
        self, command: object, caps: object = None, *, workdir: object = None
    ) -> SpawnOutcome:
        with self._cv:
            self.launched.append(list(command))  # type: ignore[call-overload]
            self._cv.notify_all()
        return SpawnOutcome(exit_code=0)

    def wait_for(self, count: int, timeout: float = 5.0) -> None:
        with self._cv:
            assert self._cv.wait_for(lambda: len(self.launched) >= count, timeout)


class _AdapterReporter:
    def __init__(self, run: Run) -> None:
        self._run = run
        self.transitions: list[RunState] = []

    def get(self, run_id: str, *, ctx: object = None) -> Run:
        return self._run

    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        detail: str | None = None,
        failure_reason: str | None = None,
        result: object | None = None,
        ctx: object = None,
    ) -> Run:
        self.transitions.append(target)
        return self._run

    def record_usage(
        self, run_id: str, report: UsageReport, *, ctx: object = None
    ) -> Run:
        return self._run

    def record_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        detail: str | None = None,
        ctx: object = None,
    ) -> None:
        return None


def test_escalated_sandbox_run_redrives_in_subprocess(
    make_manifest: Callable[..., AgentWorkload],
    tmp_path: Path,
) -> None:
    (tmp_path / "sandbox_worker.py").write_text(
        "def run(task, ctx):\n    return {'ok': True}\n", encoding="utf-8"
    )
    workload = make_manifest(
        name="agent-1",
        runtime={"adapter": "raw-worker", "entrypoint": "sandbox_worker:run"},
        sandbox={
            "enabled": True,
            "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
        },
    )
    run = Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.RUNNING,
        context=AdmissionContext.SANDBOX,
        created_at=_NOW,
        updated_at=_NOW,
        sandbox=True,
    )
    spawner = _RecordingSpawner()
    adapter = RawWorkerAdapter(
        _AdapterReporter(run),
        SimpleNamespace(reset_drive=lambda run_id: None),  # type: ignore[arg-type]
        EntrypointLoader(root=tmp_path),
        sandbox_channel=SandboxChannel(),
        base_url="http://control-plane",
        subprocess_spawner=spawner,  # type: ignore[arg-type]
        sandbox_mode="subprocess",
    )
    adapter.register(workload)
    context = RunContext(run=run, workload=workload, sandbox=True)

    adapter.submit(context)
    spawner.wait_for(1)

    adapter._escalated[run.id] = True
    adapter.resume(run.id)
    spawner.wait_for(2)

    assert len(spawner.launched) == 2


def test_tool_call_refused_after_cancel(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    workload = _two_destructive_workload(make_manifest)
    approvals = ApprovalService(InMemoryApprovalStore(), clock=lambda: _NOW)
    cancelled = _running_run().model_copy(update={"state": RunState.CANCELLED})
    gateway = _gateway(workload, cancelled, approvals)

    result = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.drop"))

    assert result.outcome is ToolCallOutcome.DENIED
    assert result.rule == "run_not_running"


def test_channel_token_revoked_on_terminal(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    app.state.registry_service.create(
        make_manifest(name="agent-a"), ctx=context_for_run("default")
    )
    channel: SandboxChannel = app.state.sandbox_channel
    app.state.run_service.store.save_run(
        Run(
            id="run-x",
            workload_id="agent-a",
            caller="cli",
            state=RunState.RUNNING,
            context=AdmissionContext.SANDBOX,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        ctx=context_for_run("default"),
    )
    token = channel.mint("run-x", ctx=context_for_run("default"))
    assert channel.verify("run-x", token)

    app.state.run_service.fail("run-x", actor="api", reason="boom")

    assert not channel.verify("run-x", token)


def test_subprocess_child_does_not_inherit_plane_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from hiveplane.execution.subprocess_spawner import SubprocessSpawner

    monkeypatch.setenv("HIVEPLANE_M61_PLANE_SENTINEL", "leak")
    command = [
        sys.executable,
        "-c",
        "import os, sys; "
        "sys.stderr.write(os.environ.get('HIVEPLANE_M61_PLANE_SENTINEL', 'absent'))",
    ]

    outcome = SubprocessSpawner().launch(command, None)

    assert outcome.exit_code == 0
    assert outcome.stderr == "absent"


class _CountingStore(InMemoryRunStore):
    """In-memory store that counts how often the event log is listed."""

    def __init__(self) -> None:
        super().__init__()
        self.list_events_calls = 0

    def list_events(
        self, run_id: str, *, ctx: object = DEFAULT_CONTEXT
    ) -> list[RunEvent]:
        self.list_events_calls += 1
        return super().list_events(run_id, ctx=ctx)  # type: ignore[arg-type]


def _plain_service(store: InMemoryRunStore) -> RunService:
    budget = BudgetService(InMemoryBudgetStore(), CostTable())
    return RunService(
        store,
        RegistryService(InMemoryRegistryStore()),
        admission=AdmissionPipeline(
            _Cert(True), _Policy(DecisionOutcome.ALLOW), budget, _Sandbox(True)
        ),
        fanout=_FanOut(),
        budget=budget,
    )


def test_append_event_does_not_load_event_log() -> None:
    store = _CountingStore()
    store.save_run(_running_run())
    service = _plain_service(store)

    for _ in range(100):
        service.record_event("run-1", EventType.USAGE, "adapter")

    assert store.list_events_calls == 0
    assert [event.sequence for event in store.list_events("run-1")] == list(range(100))


def test_concurrent_event_append_unique_sequences() -> None:
    store = InMemoryRunStore()
    store.save_run(_running_run())

    def append_many() -> None:
        for _ in range(50):
            store.add_event(
                RunEvent(
                    run_id="run-1",
                    sequence=0,
                    type=EventType.USAGE,
                    actor="adapter",
                    timestamp=_NOW,
                )
            )

    threads = [threading.Thread(target=append_many) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    sequences = sorted(event.sequence for event in store.list_events("run-1"))
    assert sequences == list(range(100))


def test_velocity_guard_prunes_old_events() -> None:
    from datetime import timedelta

    from hiveplane.guards.velocity import SpendVelocityGuard

    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    guard = SpendVelocityGuard(clock=lambda: now[0], retention_seconds=60)

    for _ in range(100):
        guard.record("agent-1", 1.0, window_seconds=60)
    assert guard.event_count("agent-1") == 100

    now[0] = now[0] + timedelta(seconds=120)
    guard.record("agent-1", 1.0, window_seconds=60)

    assert guard.event_count("agent-1") == 1


def test_context_guard_release_drops_accounting() -> None:
    from hiveplane.guards.context import ContextBudgetGuard

    guard = ContextBudgetGuard()
    guard.record("run-1", step=0, model_identity="m", input_tokens=2, output_tokens=3)
    assert guard.total("run-1") == 5

    guard.release("run-1")

    assert guard.total("run-1") == 0
    assert guard.accounts("run-1") == []


def test_context_limit_is_per_workload(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(make_manifest(name="light", guards={"context_tokens": 100}))
    registry.create(make_manifest(name="heavy", guards={"context_tokens": 10_000}))
    manager = GuardManager(
        ContextBudgetGuard(),
        SpendVelocityGuard(),
        limit_lookup=build_guard_limit_lookup(registry, GuardLimits(context_tokens=500)),
    )
    report = UsageReport(
        run_id="r",
        input_tokens=600,
        output_tokens=0,
        tool_calls=0,
        cost_usd=0.0,
        timestamp=_NOW,
    )
    light = _running_run().model_copy(
        update={"id": "r-light", "workload_id": "light"}
    )
    heavy = _running_run().model_copy(
        update={"id": "r-heavy", "workload_id": "heavy"}
    )

    assert manager.on_usage(light, report) is not None
    assert manager.on_usage(heavy, report) is None


def test_approved_calls_are_reused_across_a_redrive(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    workload = _two_destructive_workload(make_manifest)
    approvals = ApprovalService(InMemoryApprovalStore(), clock=lambda: _NOW)
    gateway = _gateway(workload, _running_run(), approvals)

    drop = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.drop"))
    assert drop.outcome is ToolCallOutcome.ESCALATED
    assert drop.approval_id is not None
    approvals.decide(drop.approval_id, status=ApprovalStatus.APPROVED, operator="op")

    gateway.reset_drive("run-1")
    assert gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.drop")).outcome is (
        ToolCallOutcome.ALLOWED
    )
    delete = gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.delete"))
    assert delete.outcome is ToolCallOutcome.ESCALATED
    assert delete.approval_id is not None
    approvals.decide(delete.approval_id, status=ApprovalStatus.APPROVED, operator="op")

    # A redrive replays both calls; each approved approval is reused, not
    # re-escalated.
    gateway.reset_drive("run-1")
    assert gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.drop")).outcome is (
        ToolCallOutcome.ALLOWED
    )
    assert gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.delete")).outcome is (
        ToolCallOutcome.ALLOWED
    )
