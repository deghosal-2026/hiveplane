"""Composition root for the run lifecycle services."""

from __future__ import annotations

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
from hiveplane.registry.service import RegistryService


def build_run_store() -> RunStore:
    """Build the configured run store (durable JSON by default)."""
    settings = get_settings()
    if settings.execution.store == "json":
        return JsonFileRunStore(settings.execution.data_dir)
    return InMemoryRunStore()


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
    )
