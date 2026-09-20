"""Composition root for the run lifecycle services."""

from __future__ import annotations

from pathlib import Path

from hiveplane.adapters.base import AdapterRunExecutor
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.adapters.raw_worker import RawWorkerAdapter, Spawner
from hiveplane.config import get_settings
from hiveplane.core.fanout import FanOutType
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.fanout import FanOutService, SlackTransport, WebhookTransport
from hiveplane.execution.gates import (
    ApprovalRequests,
    BudgetGate,
    ManifestSandboxGate,
    NullRunExecutor,
    PolicyGate,
    RegistryCertificationGate,
    SandboxRuntime,
)
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore, JsonFileRunStore, RunStore
from hiveplane.execution.tool_executor import FixtureToolExecutor
from hiveplane.execution.tools import ToolGateway
from hiveplane.persistence.audit import AuditLog, InMemoryAuditLog
from hiveplane.persistence.base import create_engine_from_settings
from hiveplane.persistence.postgres_audit import PostgresAuditLog
from hiveplane.persistence.run_store import PostgresRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.shaping.injection import InjectionScanner
from hiveplane.shaping.pipeline import ShapingPipeline


def build_run_store(store: str | None = None) -> RunStore:
    """Build the configured run store (durable JSON by default)."""
    settings = get_settings()
    selected = store or settings.execution.store
    if selected == "postgres":
        return PostgresRunStore(create_engine_from_settings(settings))
    if selected == "json":
        return JsonFileRunStore(settings.execution.data_dir)
    return InMemoryRunStore()


def build_audit_log() -> AuditLog:
    """Build the configured audit log (Postgres when the store is Postgres)."""
    settings = get_settings()
    if settings.execution.store == "postgres":
        return PostgresAuditLog(create_engine_from_settings(settings))
    return InMemoryAuditLog()


def build_run_service(
    registry_service: RegistryService,
    policy_gate: PolicyGate,
    approvals: ApprovalRequests,
    budget_gate: BudgetGate,
    sandbox_runtime: SandboxRuntime,
) -> RunService:
    """Build a RunService with the real policy and budget gates."""
    settings = get_settings()
    store = build_run_store()
    fanout = FanOutService(
        store,
        {
            FanOutType.SLACK: SlackTransport(webhook_url=settings.fanout.slack_webhook_url),
            FanOutType.WEBHOOK: WebhookTransport(
                default_url=settings.fanout.generic_webhook_url
            ),
        },
        enabled=settings.fanout.enabled,
        max_retries=settings.fanout.max_retries,
    )
    return RunService(
        store,
        registry_service,
        admission=AdmissionPipeline(
            RegistryCertificationGate(registry_service),
            policy_gate,
            budget_gate,
            ManifestSandboxGate(),
        ),
        executor=NullRunExecutor(),
        fanout=fanout,
        approvals=approvals,
        budget=budget_gate,
        sandbox_runtime=sandbox_runtime,
        audit=build_audit_log() if settings.execution.store == "postgres" else None,
    )


def build_tool_gateway(
    registry_service: RegistryService,
    policy_gate: PolicyGate,
    run_service: RunService,
    approvals: ApprovalRequests | None,
) -> ToolGateway:
    """Build the tool-call boundary over the live run service.

    Tool calls without a caller-provided output are served from the configured
    fixture store so agents never fabricate tool data (M23, #134). An empty
    ``tool_fixtures`` setting disables the executor (caller output only).
    """
    settings = get_settings()
    executor: FixtureToolExecutor | None = (
        FixtureToolExecutor(settings.execution.tool_fixtures)
        if settings.execution.tool_fixtures
        else None
    )
    return ToolGateway(
        registry_service,
        policy_gate,
        run_service,
        shaping=ShapingPipeline(InjectionScanner()),
        approvals=approvals,
        executor=executor,
    )


def attach_raw_worker(
    run_service: RunService,
    tool_gateway: ToolGateway,
    *,
    root: str | Path | None = None,
    spawner: Spawner | None = None,
) -> RawWorkerAdapter:
    """Build the raw-worker adapter and bind it as the run service's executor."""
    settings = get_settings()
    adapter = RawWorkerAdapter(
        run_service,
        tool_gateway,
        EntrypointLoader(root=root or settings.execution.entrypoints_root),
        spawner=spawner,
    )
    run_service.attach_executor(AdapterRunExecutor(adapter))
    return adapter
