"""Composition root for the run lifecycle services."""

from __future__ import annotations

from hiveplane.config import get_settings
from hiveplane.core.fanout import FanOutType
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.fanout import FanOutService, SlackTransport, WebhookTransport
from hiveplane.execution.gates import (
    ApprovalRequests,
    ManifestSandboxGate,
    NullRunExecutor,
    PolicyGate,
    RegistryCertificationGate,
    UnlimitedBudgetGate,
)
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService


def build_run_service(
    registry_service: RegistryService,
    policy_gate: PolicyGate,
    approvals: ApprovalRequests,
) -> RunService:
    """Build a RunService with the real policy gate and approval seam."""
    settings = get_settings()
    store = InMemoryRunStore()
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
            UnlimitedBudgetGate(),
            ManifestSandboxGate(),
        ),
        executor=NullRunExecutor(),
        fanout=fanout,
        approvals=approvals,
    )
