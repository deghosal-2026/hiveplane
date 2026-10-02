"""FastAPI application factory for the HivePlane control plane.

M1 provides the health surface; M3-M4 add the registry API (workload CRUD,
fleet catalog, versioning, and dry-run). Execution, policy, and intervention
endpoints arrive in later milestones.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from hiveplane import __version__, telemetry
from hiveplane.a2a import A2AAdapter
from hiveplane.agent_tools.engine import AgentToolInvoker
from hiveplane.agent_tools.registry import AgentToolRegistry
from hiveplane.agent_tools.store import build_agent_tool_store
from hiveplane.api.a2a import router as a2a_router
from hiveplane.api.adapters import router as adapters_router
from hiveplane.api.agent_tools import router as agent_tools_router
from hiveplane.api.approvals import apply_approval_decision
from hiveplane.api.approvals import router as approvals_router
from hiveplane.api.artifacts import router as artifacts_router
from hiveplane.api.ask import router as ask_router
from hiveplane.api.backup import router as backup_router
from hiveplane.api.certifications import router as certifications_router
from hiveplane.api.cluster import router as cluster_router
from hiveplane.api.corpora import router as corpora_router
from hiveplane.api.cost import router as cost_router
from hiveplane.api.delivery import router as delivery_router
from hiveplane.api.demo import router as demo_router
from hiveplane.api.drift import router as drift_router
from hiveplane.api.errors import RequestIdMiddleware, install_error_handlers
from hiveplane.api.events import router as events_router
from hiveplane.api.federation import router as federation_router
from hiveplane.api.fleet import router as fleet_router
from hiveplane.api.health import router as health_router
from hiveplane.api.identity import router as identity_router
from hiveplane.api.learning import router as learning_router
from hiveplane.api.mcp import router as mcp_router
from hiveplane.api.pipelines import router as pipelines_router
from hiveplane.api.policy import router as policy_router
from hiveplane.api.progressive import router as progressive_router
from hiveplane.api.promotions import router as promotions_router
from hiveplane.api.ratelimit import RateLimitMiddleware, TenantRateLimiter
from hiveplane.api.readiness import build_readiness_probe
from hiveplane.api.reconcile import router as reconcile_router
from hiveplane.api.registry import router as registry_router
from hiveplane.api.replay import router as replay_router
from hiveplane.api.reporting import router as reporting_router
from hiveplane.api.router import router as route_router
from hiveplane.api.runs import router as runs_router
from hiveplane.api.sandbox_channel import SandboxChannel
from hiveplane.api.sandbox_channel import router as sandbox_router
from hiveplane.api.scheduler import router as scheduler_router
from hiveplane.api.search import router as search_router
from hiveplane.api.security import router as security_router
from hiveplane.api.services import router as services_router
from hiveplane.api.spend import router as spend_router
from hiveplane.api.tenants import router as tenants_router
from hiveplane.api.transparency import router as transparency_router
from hiveplane.api.triggers import router as triggers_router
from hiveplane.api.v2 import router as v2_router
from hiveplane.api.workers import router as workers_router
from hiveplane.artifacts.backend import build_blob_backend
from hiveplane.artifacts.service import ArtifactService, RetentionService
from hiveplane.artifacts.store import build_artifact_store
from hiveplane.auth.service import build_auth_service
from hiveplane.backup.service import BackupService
from hiveplane.backup.targets import registry_store_target, run_store_target
from hiveplane.budget.errors import MissingModelIdentityError, UnknownModelPriceError
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import build_budget_store
from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.errors import (
    CertificationNotFoundError,
    CorpusError,
    ExecutorNotConfiguredError,
)
from hiveplane.certification.models import CertificationPolicy, Environment, Thresholds
from hiveplane.certification.promotion import PromotionGate
from hiveplane.certification.promotion_store import build_promotion_store
from hiveplane.certification.runner import (
    ReferenceExecutor,
    TaskExecutor,
    UnconfiguredTaskExecutor,
)
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair, load_or_generate_keypair
from hiveplane.certification.store import build_certification_store
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.chaos.factory import build_chaos_engine
from hiveplane.config import Settings, get_settings
from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.manifest import manifest_json_schema
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.spec import IOSpec
from hiveplane.corpus.service import CorpusService
from hiveplane.corpus.store import build_corpus_release_store
from hiveplane.cost.depth import CostDepthError, CostEstimator, ResultCache
from hiveplane.cost.service import CostService
from hiveplane.cost.store import CostStore, build_cost_store
from hiveplane.defense.escalation import AttemptEscalator
from hiveplane.defense.events import build_security_event_store
from hiveplane.defense.guard import DefenseGuard
from hiveplane.defense.scanner import DefenseScanner, DetectorConfig, DetectorSeverity
from hiveplane.defense.taint import TaintRegistry
from hiveplane.delivery.approvals import InteractiveResolution
from hiveplane.delivery.factory import build_delivery_service
from hiveplane.delivery.service import (
    DeliveryService,
    EscalationPolicy,
    EscalationService,
)
from hiveplane.demo.seed import DemoSeeder
from hiveplane.drift.detector import DriftDetector
from hiveplane.drift.errors import (
    DriftError,
    DriftNotConfiguredError,
    QuarantineNotFoundError,
    ReinstatementRefusedError,
)
from hiveplane.drift.monitor import DriftMonitor
from hiveplane.drift.notify import DriftNotifier
from hiveplane.drift.quarantine import QuarantineService
from hiveplane.drift.reinstatement import ReinstatementService
from hiveplane.drift.scheduler import DriftScheduler
from hiveplane.drift.store import build_drift_store
from hiveplane.events.sender import HttpEventSender
from hiveplane.events.service import FleetEventService
from hiveplane.events.store import build_event_subscription_store
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.fanout import DeliveryTransport, SlackTransport, WebhookTransport
from hiveplane.execution.recovery import RunRecovery
from hiveplane.execution.service import RunService
from hiveplane.execution.subprocess_spawner import SubprocessSpawner
from hiveplane.execution.wiring import (
    attach_auto_adapters,
    attach_langgraph,
    attach_openai_agents,
    attach_pydanticai,
    attach_raw_worker,
    build_audit_log,
    build_guard_limit_lookup,
    build_run_service,
    build_tool_gateway,
)
from hiveplane.federation.service import FederationService
from hiveplane.federation.store import build_remote_plane_store
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.guards.breaker import CircuitBreakerRegistry
from hiveplane.guards.context import ContextBudgetGuard
from hiveplane.guards.manager import GuardLimits, GuardManager
from hiveplane.guards.velocity import SpendVelocityGuard
from hiveplane.ha.leader import LeaderElector
from hiveplane.ha.store import build_leader_store
from hiveplane.health.models import SloTarget
from hiveplane.health.plane_metrics import PlaneMetrics
from hiveplane.health.service import HealthService
from hiveplane.incident.notify import IncidentBroadcaster
from hiveplane.incident.service import IncidentHaltGate, IncidentService
from hiveplane.incident.store import build_incident_store
from hiveplane.learning.candidate_store import build_candidate_store
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.corpus_store import build_corpus_version_store
from hiveplane.learning.corpus_versions import CorpusVersionService
from hiveplane.learning.errors import (
    CandidateAlreadyExistsError,
    CandidateAlreadyReviewedError,
    CandidateNotAllowedError,
    CandidateNotFoundError,
    FeedbackNotAllowedError,
    FeedbackNotFoundError,
)
from hiveplane.learning.eval import EvalService
from hiveplane.learning.eval_store import build_eval_store
from hiveplane.learning.feedback import FeedbackService
from hiveplane.learning.judge import RubricJudge
from hiveplane.learning.rubrics import RubricRegistry, default_rubric
from hiveplane.learning.store import build_feedback_store
from hiveplane.llm.factory import build_provider
from hiveplane.mcp.executor import McpToolExecutor
from hiveplane.mcp.factory import build_mcp_registry
from hiveplane.persistence.base import create_engine_from_settings
from hiveplane.persistence.migrate import run_migrations
from hiveplane.pipelines.engine import PipelineEngine
from hiveplane.pipelines.executor import (
    RunNodeExecutor,
    ServiceApprovalGate,
    TriggerPipelineSubmitter,
)
from hiveplane.pipelines.store import build_pipeline_store
from hiveplane.plugins.registry import PluginRegistry
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    PolicyPackAlreadyExistsError,
    PolicyPackNotFoundError,
)
from hiveplane.policy.kill_switch import KillSwitch, build_kill_switch_store
from hiveplane.policy.pack_registry import PolicyPackRegistry
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.policy.store import build_approval_store
from hiveplane.probes.models import ProbeOutcome, ProbeSpec
from hiveplane.probes.service import ProbeService
from hiveplane.progressive.canary import CanaryService
from hiveplane.progressive.errors import (
    CanaryNotAllowedError,
    CanaryNotFoundError,
    ExperimentNotFoundError,
    ShadowBudgetExceededError,
    ShadowNotFoundError,
)
from hiveplane.progressive.experiments import ExperimentService
from hiveplane.progressive.runner import RunShadowRunner
from hiveplane.progressive.shadow import ShadowService
from hiveplane.progressive.store import build_progressive_store
from hiveplane.reconcile.conflict import ConflictPolicy
from hiveplane.reconcile.controller import ReconcileController
from hiveplane.reconcile.executor import ActionExecutor
from hiveplane.reconcile.locking import InMemoryReconcileLock, PostgresReconcileLock
from hiveplane.reconcile.observe import ServiceObserver
from hiveplane.reconcile.planner import Guardrails
from hiveplane.reconcile.store import build_reconcile_store
from hiveplane.registry.errors import (
    AdmissionRefusedError,
    AttestationAlreadyExistsError,
    AttestationNotFoundError,
    AttestationVerificationError,
    DestructiveToolRequiresApprovalError,
    ReCertificationRequiredError,
    ToolAlreadyExistsError,
    UnknownToolError,
    VersionNotFoundError,
    WorkloadAlreadyExistsError,
    WorkloadInUseError,
    WorkloadNotFoundError,
)
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import build_registry_store
from hiveplane.replay.errors import ReplayNotFoundError
from hiveplane.replay.service import ReplayService
from hiveplane.replay.store import build_replay_store
from hiveplane.reporting.audit_export import AuditExportService
from hiveplane.reporting.digest import DigestService
from hiveplane.reporting.evidence import EvidencePackService
from hiveplane.reporting.factory import build_reporting_store
from hiveplane.reporting.pii import PIIScrubber
from hiveplane.reporting.purge import PurgeTarget, TenantPurgeService
from hiveplane.reporting.retention import RetentionEnforcer
from hiveplane.reporting.scheduler import DigestScheduler
from hiveplane.router.catalog import RegistryCatalog
from hiveplane.router.classifier import LLMTaskClassifier
from hiveplane.router.engine import RouterEngine
from hiveplane.router.store import build_router_store
from hiveplane.sandbox.manager import InMemorySandboxManager
from hiveplane.scheduler.scheduler import Scheduler, SchedulerConfig
from hiveplane.secrets.factory import build_secret_service
from hiveplane.secrets.injection import SecretInjector
from hiveplane.secrets.redaction import RedactionLogFilter, Redactor, RedactorRegistry
from hiveplane.tenancy import Tenant, TenantContext
from hiveplane.tenancy.admin import TenantAdminService
from hiveplane.tenancy.context import (
    DEFAULT_CONTEXT,
    DEFAULT_TENANT_ID,
    SYSTEM_CONTEXT,
    context_for_run,
)
from hiveplane.tenancy.errors import (
    TeamNotFoundError,
    TenantAlreadyExistsError,
    TenantNotFoundError,
    TenantScopeError,
    TenantSuspendedError,
)
from hiveplane.tenancy.store import InMemoryTenantStore, TenantStore, build_tenant_store
from hiveplane.transparency import (
    PublicVerifier,
    SigningKeyRegistry,
    TransparencyLog,
    build_signing_key_store,
    build_transparency_store,
)
from hiveplane.triggers.engine import TriggerEngine
from hiveplane.triggers.freeze import FreezeService, build_freeze_store
from hiveplane.triggers.ingest import WebhookVerifier
from hiveplane.triggers.limiter import TriggerLimiter
from hiveplane.triggers.runner import TriggerRunner
from hiveplane.triggers.scheduler import TriggerScheduler
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import build_trigger_store
from hiveplane.triggers.watch import WatchRunner
from hiveplane.worker.factory import build_worker_registry
from hiveplane.worker.models import RunAssignment
from hiveplane.worker.registry import WorkerRegistry

_LOGGER = logging.getLogger(__name__)


def _attach_redaction_filter(redactor: Redactor) -> None:
    """Redact resolved secret values from every log record (#540).

    A logging filter on a logger only sees records emitted by that logger, so
    the filter is attached to the root handlers (all sinks) as well as the root
    logger. Earlier filters are replaced so the latest live redactor wins.
    """
    flt = RedactionLogFilter(redactor)
    root = logging.getLogger()
    root.filters = [f for f in root.filters if not isinstance(f, RedactionLogFilter)]
    root.addFilter(flt)
    for handler in root.handlers:
        handler.filters = [f for f in handler.filters if not isinstance(f, RedactionLogFilter)]
        handler.addFilter(flt)


#: Interval of the opt-in digest background ticker, in seconds.
_DIGEST_TICK_SECONDS = 60

#: Interval of the pipeline-advance background ticker, in seconds.
_PIPELINE_TICK_SECONDS = 5

#: Base interval of the cron/watch trigger ticker, in seconds (D23 60s tick).
_TRIGGER_TICK_SECONDS = 60

#: Maximum random jitter added to each trigger tick, in seconds.
_TRIGGER_TICK_JITTER_SECONDS = 10.0

#: Interval of the worker lease-expiry/crash reclaim ticker, in seconds.
_WORKER_RECLAIM_TICK_SECONDS = 15

#: Interval of the background retention purge sweep, in seconds.
_RETENTION_TICK_SECONDS = 3600

#: Interval of the synthetic-probe sweep, in seconds.
_PROBE_TICK_SECONDS = 60
#: Run states that mean a probe run has finished (and its result is final).
_PROBE_TERMINAL = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})

#: Interval of the budget-period materialization sweep, in seconds.
_BUDGET_ROLLOVER_TICK_SECONDS = 3600

#: Interval of the burn-through health sweep, in seconds.
_HEALTH_TICK_SECONDS = 60


class _RunServiceProbeRunner:
    """Runs a synthetic probe as an isolated, non-delivering probe run (M43)."""

    def __init__(self, run_service: RunService) -> None:
        self._run_service = run_service

    def run(self, spec: ProbeSpec) -> ProbeOutcome:
        """Run a synthetic probe as an isolated, non-delivering probe run (M43).

        A refused admission is a failed probe. When the spec declares an
        ``expected`` output, the run is started and its result compared, so a probe
        can flag behavioural decay before the drift threshold would trip.
        """
        ctx = context_for_run(spec.tenant_id)
        try:
            run = self._run_service.submit(
                workload=spec.workload_id,
                caller="probe",
                context=AdmissionContext.SANDBOX,
                task=spec.input,
                probe=True,
                ctx=ctx,
            )
        except Exception as exc:
            return ProbeOutcome(passed=False, detail={"error": str(exc)})
        if spec.expected is None:
            return ProbeOutcome(passed=True, detail={"admitted": True})
        try:
            self._run_service.start(run.id, actor="probe", ctx=ctx)
            # The adapter executes asynchronously; wait briefly for a terminal state
            # so the probe can compare the agent's output to the expectation.
            deadline = time.monotonic() + 30
            finished = self._run_service.get(run.id, ctx=ctx)
            while finished.state not in _PROBE_TERMINAL and time.monotonic() < deadline:
                time.sleep(0.2)
                finished = self._run_service.get(run.id, ctx=ctx)
        except Exception as exc:
            return ProbeOutcome(passed=False, detail={"error": str(exc)})
        return ProbeOutcome(
            passed=finished.result == spec.expected,
            detail={
                "state": finished.state.value,
                "result": finished.result,
                "expected": spec.expected,
            },
        )


#: Registry errors mapped to HTTP status codes.
_ERROR_STATUS: tuple[tuple[type[Exception], int], ...] = (
    (WorkloadNotFoundError, 404),
    (VersionNotFoundError, 404),
    (AttestationNotFoundError, 404),
    (WorkloadAlreadyExistsError, 409),
    (WorkloadInUseError, 409),
    (ReCertificationRequiredError, 409),
    (ToolAlreadyExistsError, 409),
    (AttestationAlreadyExistsError, 409),
    (AdmissionRefusedError, 403),
    (UnknownToolError, 422),
    (DestructiveToolRequiresApprovalError, 422),
    (AttestationVerificationError, 422),
    (CertificationNotFoundError, 404),
    (CorpusError, 422),
    (ExecutorNotConfiguredError, 503),
    (QuarantineNotFoundError, 404),
    (ReinstatementRefusedError, 409),
    (DriftNotConfiguredError, 409),
    (DriftError, 422),
    (TenantScopeError, 403),
    (TenantNotFoundError, 404),
    (TeamNotFoundError, 404),
    (TenantAlreadyExistsError, 409),
    (TenantSuspendedError, 403),
    (CostDepthError, 422),
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Migrate the system of record, then install the OpenTelemetry pipeline."""
    settings = get_settings()
    if settings.execution.store == "postgres":
        run_migrations(settings)
    key_registry = getattr(app.state, "signing_key_registry", None)
    verifier_key = getattr(app.state, "artifact_verification_key", None)
    key_id = getattr(app.state, "artifact_signing_key_id", None)
    if key_registry is not None and verifier_key is not None and key_id is not None:
        # Register the attestation/bundle signing key now that the schema exists
        # (deferred from create_app so a fresh database can boot, M61).
        key_registry.add_key(key_id, verifier_key)
    tenant_store = getattr(app.state, "tenant_store", None)
    if tenant_store is not None:
        _seed_default_tenant(tenant_store)
    provider = telemetry.configure_telemetry(settings.otel)
    meter_provider = telemetry.configure_metrics(settings.otel)
    recovery = getattr(app.state, "run_recovery", None)
    if recovery is not None:
        report = recovery.run()
        if report.reattached or report.failed or report.skipped:
            _LOGGER.info(
                "startup recovery: reattached=%s failed=%s skipped=%s",
                report.reattached,
                report.failed,
                report.skipped,
            )
    ticker: asyncio.Task[None] | None = None
    scheduler = getattr(app.state, "digest_scheduler", None)
    if settings.reporting.enabled and scheduler is not None:
        ticker = asyncio.create_task(_run_digest_ticker(scheduler))
    pipeline_engine = getattr(app.state, "pipeline_engine", None)
    pipeline_ticker: asyncio.Task[None] | None = None
    if pipeline_engine is not None:
        pipeline_ticker = asyncio.create_task(_run_pipeline_ticker(pipeline_engine))
    trigger_runner = getattr(app.state, "trigger_runner", None)
    trigger_ticker: asyncio.Task[None] | None = None
    if trigger_runner is not None:
        trigger_ticker = asyncio.create_task(_run_trigger_ticker(trigger_runner))
    reclaimer: asyncio.Task[None] | None = None
    worker_registry = getattr(app.state, "worker_registry", None)
    leader_elector = getattr(app.state, "leader_elector", None)
    if worker_registry is not None and leader_elector is not None:
        reclaimer = asyncio.create_task(_run_worker_reclaimer(worker_registry, leader_elector))
    retention_ticker: asyncio.Task[None] | None = None
    retention_enforcer = getattr(app.state, "retention_enforcer", None)
    if retention_enforcer is not None:
        retention_ticker = asyncio.create_task(_run_retention_ticker(retention_enforcer))
    probe_ticker: asyncio.Task[None] | None = None
    probe_service = getattr(app.state, "probe_service", None)
    if probe_service is not None:
        probe_ticker = asyncio.create_task(_run_probe_ticker(probe_service))
    rollover_ticker: asyncio.Task[None] | None = None
    cost_service = getattr(app.state, "cost_service", None)
    cost_store = getattr(app.state, "cost_store", None)
    if cost_service is not None and cost_store is not None:
        rollover_ticker = asyncio.create_task(_run_budget_rollover_ticker(cost_service, cost_store))
    health_ticker: asyncio.Task[None] | None = None
    health_service = getattr(app.state, "health_service", None)
    if health_service is not None:
        health_ticker = asyncio.create_task(_run_health_ticker(health_service))
    escalation: EscalationService | None = getattr(app.state, "escalation_service", None)
    escalation_ticker: asyncio.Task[None] | None = None
    if escalation is not None:
        escalation_ticker = asyncio.create_task(
            _run_escalation_ticker(
                escalation,
                app.state.leader_elector,
                settings.fanout.escalation_tick_seconds,
            )
        )
    batch_ticker = asyncio.create_task(
        _run_delivery_batch_ticker(app.state.delivery_service, _DIGEST_TICK_SECONDS)
    )
    yield
    for task in (
        ticker,
        pipeline_ticker,
        trigger_ticker,
        reclaimer,
        retention_ticker,
        probe_ticker,
        rollover_ticker,
        health_ticker,
        escalation_ticker,
        batch_ticker,
    ):
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    mcp_registry = getattr(app.state, "mcp_registry", None)
    if mcp_registry is not None:
        mcp_registry.close()
    provider.force_flush()
    meter_provider.force_flush()


async def _run_digest_ticker(scheduler: DigestScheduler) -> None:
    """Periodically fire matured digest schedules; a failure never kills the app."""
    while True:
        await asyncio.sleep(_DIGEST_TICK_SECONDS)
        try:
            scheduler.run_all_due(ctx=SYSTEM_CONTEXT)
        except Exception:
            _LOGGER.exception("digest scheduler tick failed")


async def _run_pipeline_ticker(engine: PipelineEngine) -> None:
    """Advance non-terminal pipeline runs as their async children finish."""
    while True:
        await asyncio.sleep(_PIPELINE_TICK_SECONDS)
        try:
            engine.advance_pending()
        except Exception:
            _LOGGER.exception("pipeline driver tick failed")


async def _run_trigger_ticker(runner: TriggerRunner) -> None:
    """Fire due cron/watch triggers on a jittered tick; failures never kill it."""
    while True:
        await asyncio.sleep(
            _TRIGGER_TICK_SECONDS + random.uniform(0.0, _TRIGGER_TICK_JITTER_SECONDS)
        )
        try:
            runner.tick()
        except Exception:
            _LOGGER.exception("trigger ticker tick failed")


def run_worker_reclaim_cycle(
    registry: WorkerRegistry, elector: LeaderElector
) -> list[RunAssignment]:
    """Run one leader-gated lease-expiry/crash reclaim pass (M46-03/04, D34).

    Only the active leader reassigns leases, so two controller replicas cannot
    double-assign the same run. Standbys return immediately.
    """
    if elector.is_leader():
        elector.renew()
    elif not elector.acquire():
        return []
    registry.mark_unhealthy()
    return registry.reclaim()


async def _run_worker_reclaimer(registry: WorkerRegistry, elector: LeaderElector) -> None:
    """Periodically reclaim expired/crashed worker leases (M46-03/04)."""
    while True:
        await asyncio.sleep(_WORKER_RECLAIM_TICK_SECONDS)
        try:
            run_worker_reclaim_cycle(registry, elector)
        except Exception:
            _LOGGER.exception("worker reclaim tick failed")


async def _retention_tick(enforcer: RetentionEnforcer) -> None:
    """Run one retention sweep over every tenant with a persisted policy."""
    try:
        enforcer.purge_due()
    except Exception:
        _LOGGER.exception("retention purge tick failed")


async def _run_retention_ticker(enforcer: RetentionEnforcer) -> None:
    """Periodically purge expired data; a failure never kills the app."""
    while True:
        await asyncio.sleep(_RETENTION_TICK_SECONDS)
        await _retention_tick(enforcer)


async def _run_probe_ticker(service: ProbeService) -> None:
    """Periodically run matured synthetic probes on every tenant's schedule."""
    while True:
        await asyncio.sleep(_PROBE_TICK_SECONDS)
        try:
            service.run_due(ctx=SYSTEM_CONTEXT)
        except Exception:
            _LOGGER.exception("probe sweep tick failed")


async def _budget_rollover_tick(service: CostService, store: CostStore) -> None:
    """Materialize the next budget period for every tenant and period kind."""
    try:
        for tenant_id in store.period_tenants():
            ctx = context_for_run(tenant_id)
            for kind in CostPeriodKind:
                service.rollover(tenant_id, kind, ctx=ctx)
    except Exception:
        _LOGGER.exception("budget rollover tick failed")


async def _run_budget_rollover_ticker(service: CostService, store: CostStore) -> None:
    """Periodically materialize budget periods; a failure never kills the app."""
    while True:
        await asyncio.sleep(_BUDGET_ROLLOVER_TICK_SECONDS)
        await _budget_rollover_tick(service, store)


async def _health_tick(service: HealthService) -> None:
    """Run one automatic burn-through evaluation over every workload."""
    try:
        service.sweep(ctx=SYSTEM_CONTEXT)
    except Exception:
        _LOGGER.exception("health burn-through tick failed")


async def _run_health_ticker(service: HealthService) -> None:
    """Periodically enforce burn-through; a failure never kills the app."""
    while True:
        await asyncio.sleep(_HEALTH_TICK_SECONDS)
        await _health_tick(service)


async def _run_escalation_ticker(
    escalation: EscalationService, elector: LeaderElector, interval: float
) -> None:
    """Escalate unanswered approvals, but only on the leader (M51-05)."""
    while True:
        await asyncio.sleep(interval)
        if not elector.is_leader():
            continue
        try:
            escalation.check()
        except Exception:
            _LOGGER.exception("escalation tick failed")


async def _run_delivery_batch_ticker(delivery: DeliveryService, interval: float) -> None:
    """Flush matured BATCHED deliveries so batched events are never dropped."""
    while True:
        await asyncio.sleep(interval)
        try:
            delivery.flush_due()
        except Exception:
            _LOGGER.exception("delivery batch flush failed")


def _build_escalation_service(settings: Settings, app: FastAPI) -> EscalationService | None:
    """Build and bind the on-call escalation service when targets are configured."""
    targets = settings.fanout.escalation_targets
    if not targets:
        return None

    def _notify(approval_id: str, target: str, reason: str) -> None:
        app.state.audit_log.append(
            "escalation",
            "approval.escalated",
            approval_id,
            detail=f"target={target} reason={reason}",
        )

    service = EscalationService(
        EscalationPolicy(
            targets=targets,
            response_window_s=settings.fanout.escalation_response_window_s,
        ),
        _notify,
    )
    app.state.approval_service.bind_escalation(
        lambda approval_id, tenant_id: service.register(approval_id, tenant_id=tenant_id),
        service.respond,
    )
    return service


def _seed_default_tenant(store: TenantStore) -> None:
    """Ensure the default tenant exists so header-less callers can act (M58-06)."""
    if store.get_tenant(SYSTEM_CONTEXT, DEFAULT_TENANT_ID) is None:
        store.save_tenant(
            SYSTEM_CONTEXT,
            Tenant(
                tenant_id=DEFAULT_TENANT_ID,
                name="Default",
                created_at=datetime.now(UTC),
            ),
        )


def create_app(
    registry_service: RegistryService | None = None,
    run_service: RunService | None = None,
    certification_coordinator: CertificationCoordinator | None = None,
) -> FastAPI:
    """Build and return the control-plane ASGI application."""
    app = FastAPI(
        title="HivePlane",
        version=__version__,
        summary="Control plane for production agent fleets.",
        lifespan=_lifespan,
    )
    app.add_middleware(telemetry.TelemetryMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    settings = get_settings()
    # Bring the schema up to head *before* constructing any store: several services
    # read Postgres during create_app (signing keys, MCP registry, leader, …), which
    # crashes a fresh deployment whose tables do not exist yet (M61). ``_lifespan``
    # re-runs this idempotently.
    if settings.execution.store == "postgres":
        run_migrations(settings)
    app.state.rate_limiter = (
        TenantRateLimiter(
            requests_per_window=settings.api.rate_limit_requests,
            window_seconds=settings.api.rate_limit_window_seconds,
        )
        if settings.api.rate_limit_enabled
        else None
    )
    if settings.certification.signing_key_file:
        private_key, public_key = load_or_generate_keypair(settings.certification.signing_key_file)
    else:
        private_key, public_key = generate_keypair()
    app.state.attestation_public_key = public_key
    app.state.artifact_signing_key = private_key
    app.state.artifact_verification_key = public_key
    app.state.artifact_signing_key_id = settings.certification.signing_key_id
    transparency_log = TransparencyLog(build_transparency_store(settings))
    app.state.transparency_log = transparency_log
    key_registry = SigningKeyRegistry(build_signing_key_store(settings))
    registry = registry_service or RegistryService(
        build_registry_store(settings),
        attestation_public_key=public_key,
        bundle_signing_key=private_key,
        bundle_key_id=settings.certification.signing_key_id,
        key_resolver=key_registry.resolve,
    )
    verifier_key = registry.attestation_public_key or public_key
    # The signing key is registered in ``_lifespan`` (after migrations run): writing
    # it here would query the database at import time and crash a fresh
    # deployment whose schema does not exist yet (# fresh-volume boot, M61).
    app.state.signing_key_registry = key_registry
    app.state.registry_service = registry
    app.state.public_verifier = PublicVerifier(
        get_attestation=registry.find_attestation,
        log=transparency_log,
        key_resolver=lambda key_id: key_registry.resolve(key_id) or verifier_key,
    )
    policy_pack_store = InMemoryPolicyPackStore()
    policy_engine = PolicyEngine(policy_pack_store)
    approval_service = ApprovalService(build_approval_store(settings))
    budget_store = build_budget_store(settings)
    if settings.budget.zero_cost_prefixes is None:
        cost_table = CostTable(settings.budget.prices)
    else:
        cost_table = CostTable(
            settings.budget.prices,
            zero_cost_prefixes=tuple(settings.budget.zero_cost_prefixes),
        )
    cost_store = build_cost_store(settings)
    cost_service = CostService(cost_store)
    budget_service = BudgetService(budget_store, cost_table, cost_service=cost_service)
    app.state.cost_store = cost_store
    app.state.cost_service = cost_service
    sandbox_manager = InMemorySandboxManager()
    app.state.policy_pack_store = policy_pack_store
    app.state.policy_pack_registry = PolicyPackRegistry(policy_pack_store)
    app.state.policy_engine = policy_engine
    app.state.approval_service = approval_service
    app.state.security_event_store = build_security_event_store(settings)
    defense: DefenseGuard | None = None
    taint_registry: TaintRegistry | None = None
    if settings.defense.enabled:
        taint_registry = TaintRegistry()
        escalator = AttemptEscalator(
            app.state.security_event_store,
            threshold=settings.defense.repeat_threshold,
            window_seconds=settings.defense.repeat_window_seconds,
            quarantine_provider=lambda: getattr(app.state, "quarantine_service", None),
        )
        defense = DefenseGuard(
            scanner=DefenseScanner(),
            events=app.state.security_event_store,
            taint=taint_registry,
            escalator=escalator,
            config=DetectorConfig(
                severity_threshold=DetectorSeverity(settings.defense.detector_severity_threshold),
                escalate_to_block=settings.defense.escalate_to_block,
            ),
        )
        app.state.defense_guard = defense
    app.state.budget_store = budget_store
    app.state.budget_service = budget_service
    app.state.cost_table = cost_table
    app.state.sandbox_manager = sandbox_manager
    app.state.sandbox_channel = SandboxChannel()

    def _drain_fleet() -> list[str]:
        """Drain admission during incident mode (freeze the queue)."""
        app.state.scheduler.freeze()
        return []

    def _undrain_fleet() -> None:
        """Lift the queue freeze when incident mode is resumed."""
        app.state.scheduler.unfreeze()

    incident_service = IncidentService(
        build_incident_store(settings),
        notifier=_build_incident_broadcaster(settings).notify,
        drain=_drain_fleet,
        undrain=_undrain_fleet,
    )
    app.state.incident_service = incident_service
    artifact_backend = build_blob_backend(settings)
    artifact_store = build_artifact_store(settings)
    app.state.artifact_backend = artifact_backend
    app.state.artifact_store = artifact_store
    app.state.reporting_store = build_reporting_store(settings)
    app.state.tenant_store = build_tenant_store(settings)
    app.state.tenant_admin_service = TenantAdminService(
        app.state.tenant_store, trusted_local=not settings.auth.enabled
    )
    if isinstance(app.state.tenant_store, InMemoryTenantStore):
        _seed_default_tenant(app.state.tenant_store)
    pii_scrubber = PIIScrubber(salt=settings.reporting.pii_salt)
    app.state.pii_scrubber = pii_scrubber
    app.state.artifact_service = ArtifactService(
        artifact_store,
        artifact_backend,
        scrubber=pii_scrubber if settings.reporting.pii_enabled else None,
    )
    app.state.retention_service = RetentionService(artifact_store, artifact_backend)
    app.state.run_service = run_service or build_run_service(
        registry,
        policy_engine,
        approval_service,
        budget_service,
        sandbox_manager,
        halt=IncidentHaltGate(incident_service),
    )
    app.state.run_service.attach_tenant_admin(app.state.tenant_admin_service)
    app.state.run_service.attach_terminal_hook(app.state.sandbox_channel.revoke)
    app.state.run_service.attach_taint(taint_registry)
    app.state.run_service.attach_defense(defense)
    app.state.run_store = app.state.run_service.store
    app.state.backup_service = BackupService(
        [
            run_store_target(app.state.run_store),
            registry_store_target(registry.store),
        ],
        private_key=app.state.artifact_signing_key,
        key_id=app.state.artifact_signing_key_id,
    )
    app.state.demo_seeder = DemoSeeder(
        app.state.tenant_admin_service,
        registry.store,
        app.state.run_store,
        app.state.cost_service,
    )
    app.state.replay_service = ReplayService(app.state.run_service, build_replay_store())
    if settings.federation.enabled:
        app.state.federation_service = FederationService(build_remote_plane_store())

    def _artifact_links(run_id: str) -> list[dict[str, Any]]:
        run = app.state.run_service.get(run_id, ctx=SYSTEM_CONTEXT)
        return [
            {
                "artifact_id": artifact.artifact_id,
                "location": artifact.location,
                "content_hash": artifact.content_hash,
                "size_bytes": artifact.size_bytes,
            }
            for artifact in app.state.artifact_service.list(
                tenant_id=run.tenant_id, run_id=run_id, ctx=SYSTEM_CONTEXT
            )
        ]

    fanout_service = getattr(app.state.run_service, "_fanout", None)
    if fanout_service is not None:
        fanout_service._artifact_lookup = _artifact_links
    kill_switch = KillSwitch(build_kill_switch_store(settings))
    app.state.kill_switch = kill_switch
    circuit_breakers = CircuitBreakerRegistry(
        failure_threshold=settings.guards.breaker_failure_threshold,
        min_calls=settings.guards.breaker_min_calls,
        open_for_seconds=settings.guards.breaker_open_for_seconds,
    )
    app.state.circuit_breakers = circuit_breakers
    app.state.mcp_registry = build_mcp_registry(settings)
    app.state.secret_service = build_secret_service(settings)
    app.state.secret_redactor = Redactor()
    app.state.secret_redactors = RedactorRegistry()
    app.state.secret_injector = SecretInjector(
        app.state.secret_service,
        app.state.secret_redactors,
        sink_redactor=app.state.secret_redactor,
    )
    _attach_redaction_filter(app.state.secret_redactor)
    app.state.auth_service = build_auth_service(
        settings=settings,
        tenant_store=app.state.tenant_store,
        require_active=app.state.tenant_admin_service.require_active,
        auth_enabled=settings.auth.enabled,
    )
    app.state.auth_store = app.state.auth_service.store
    if settings.auth.enabled and settings.auth.admin_key:
        # Seed the bootstrap admin key so the first key can be minted over HTTP.
        from hiveplane.tenancy.models import Role

        app.state.auth_service.keys.ensure_token(
            settings.auth.admin_key,
            "default",
            role=Role.ADMIN,
            label="field-test-bootstrap",
        )
    if settings.auth.enabled and settings.auth.system_key:
        # A plane-admin bootstrap key in the system tenant so it can create tenants
        # (M58); kept separate from the default-tenant admin so normal default-tenant
        # operations are unaffected.
        from hiveplane.tenancy import SYSTEM_TENANT_ID
        from hiveplane.tenancy.models import Role

        app.state.auth_service.keys.ensure_token(
            settings.auth.system_key,
            SYSTEM_TENANT_ID,
            role=Role.ADMIN,
            label="system-bootstrap",
        )
    app.state.worker_registry = build_worker_registry(settings)
    app.state.scheduler = Scheduler(SchedulerConfig())
    app.state.run_service.attach_scheduler(app.state.scheduler)
    app.state.leader_elector = LeaderElector(build_leader_store(settings))
    app.state.throttled_workloads = set()
    app.state.chaos_engine = build_chaos_engine(
        worker_registry=app.state.worker_registry, kill_switch=kill_switch
    )
    app.state.cost_estimator = CostEstimator()
    budget_service.attach_estimator(app.state.cost_estimator)

    def _valid_attestation(attestation_id: str, workload_id: str) -> bool:
        """Accept only a real, signature-verified attestation for the workload."""
        try:
            attestation = registry.get_attestation(attestation_id, ctx=SYSTEM_CONTEXT)
        except Exception:
            return False
        return attestation.workload_id == workload_id

    app.state.result_cache = ResultCache(min_attestation=_valid_attestation)

    def _resolve_interactive(resolution: InteractiveResolution) -> None:
        """Apply a token-resolved approval to its paused run, exactly as the API does."""
        apply_approval_decision(
            approval_service,
            app.state.run_service,
            resolution.approval_id,
            decision=resolution.decision,
            operator=resolution.operator_id,
            reason=resolution.reason,
            ctx=resolution.ctx,
            expected_run_id=resolution.run_id,
        )

    (
        app.state.delivery_service,
        app.state.interactive_approvals,
    ) = build_delivery_service(settings, resolve=_resolve_interactive)
    app.state.delivery_store = app.state.delivery_service.store

    def _delivery_lifecycle_gate(tenant_id: str) -> None:
        """Refuse delivery to a suspended tenant; unknown tenants pass through."""
        try:
            app.state.tenant_admin_service.require_active(DEFAULT_CONTEXT, tenant_id)
        except TenantNotFoundError:
            return

    app.state.delivery_service.bind_require_active(_delivery_lifecycle_gate)
    mcp_executor = McpToolExecutor(app.state.mcp_registry) if settings.mcp.enabled else None
    app.state.tool_gateway = build_tool_gateway(
        registry,
        policy_engine,
        app.state.run_service,
        approval_service,
        defense,
        kill_switch,
        circuit_breakers,
        mcp_executor,
    )
    if settings.guards.enabled:
        guard_limits = GuardLimits(
            context_tokens=settings.guards.context_tokens,
            context_warn_at=settings.guards.context_warn_at,
            velocity_window_seconds=settings.guards.velocity_window_seconds,
            velocity_limit_usd=settings.guards.velocity_limit_usd,
            velocity_multiplier=settings.guards.velocity_multiplier,
        )
        app.state.run_service.attach_guards(
            GuardManager(
                ContextBudgetGuard(),
                SpendVelocityGuard(),
                limit_lookup=build_guard_limit_lookup(registry, guard_limits),
            )
        )
    app.state.candidate_service = CandidateService(build_candidate_store(settings))
    app.state.corpus_version_service = CorpusVersionService(
        build_corpus_version_store(settings),
        candidates=app.state.candidate_service,
    )
    app.state.corpus_service = CorpusService(build_corpus_release_store(settings))
    app.state.plugin_registry = PluginRegistry()
    app.state.fleet_event_service = FleetEventService(
        build_event_subscription_store(settings), sender=HttpEventSender()
    )
    event_sink = app.state.fleet_event_service.publish
    app.state.run_service.bind_event_sink(event_sink)
    app.state.approval_service.bind_event_sink(event_sink)
    app.state.feedback_service = FeedbackService(
        build_feedback_store(settings),
        run_reader=app.state.run_service,
        candidates=app.state.candidate_service,
    )
    reconcile_store = build_reconcile_store(settings)
    reconcile_lock = (
        PostgresReconcileLock(create_engine_from_settings(settings))
        if settings.execution.store == "postgres"
        else InMemoryReconcileLock()
    )
    app.state.reconcile_store = reconcile_store
    app.state.reconcile_controller = ReconcileController(
        store=reconcile_store,
        observer=ServiceObserver(registry, policy_pack_store),
        executor=ActionExecutor(registry),
        lock=reconcile_lock,
        guardrails=Guardrails(
            allow_destructive=settings.reconcile.allow_destructive,
            allow_empty=settings.reconcile.allow_empty,
            max_destructive_per_run=settings.reconcile.max_destructive_per_run,
            require_destructive_confirmation=(settings.reconcile.require_destructive_confirmation),
        ),
        policy=ConflictPolicy(pinned_fields=frozenset(settings.reconcile.pinned_fields)),
    )
    trigger_store = build_trigger_store(settings)
    trigger_limiter = TriggerLimiter(
        trigger_store,
        global_max_per_minute=settings.triggers.global_max_per_minute,
        global_burst=settings.triggers.global_burst,
    )
    app.state.trigger_store = trigger_store
    app.state.trigger_secrets = dict(settings.triggers.secrets)
    app.state.webhook_verifier = WebhookVerifier(
        store=trigger_store,
        skew_seconds=settings.triggers.webhook_skew_seconds,
        replay_window_seconds=settings.triggers.replay_window_seconds,
    )
    freeze_service = FreezeService(build_freeze_store(settings))
    app.state.freeze_service = freeze_service

    def _freeze_reason(spec: TriggerSpec, ctx: TenantContext) -> str | None:
        try:
            team = registry.get(spec.target.ref).team
        except WorkloadNotFoundError:
            team = None
        freeze = freeze_service.active_for(spec.target.ref, team=team, ctx=ctx)
        if freeze is None:
            return None
        return freeze.reason or f"{freeze.scope.value} freeze active"

    pipeline_store = build_pipeline_store(settings)
    pipeline_engine = PipelineEngine(
        pipeline_store,
        RunNodeExecutor(app.state.run_service),
        approvals=ServiceApprovalGate(approval_service),
        schema_lookup=lambda workload: _workload_io(registry, workload),
        budget_lookup=lambda workload: _workload_budget(registry, workload),
    )
    app.state.pipeline_store = pipeline_store
    app.state.pipeline_engine = pipeline_engine
    app.state.trigger_engine = TriggerEngine(
        trigger_store,
        trigger_limiter,
        app.state.run_service,
        pipeline_submitter=TriggerPipelineSubmitter(pipeline_store, pipeline_engine),
        freeze_check=_freeze_reason,
    )
    app.state.trigger_scheduler = TriggerScheduler(trigger_store)
    app.state.watch_runner = WatchRunner(
        trigger_store,
        app.state.trigger_scheduler,
        app.state.trigger_engine,
        active_runs=lambda ref: len(
            app.state.run_service.list_runs(
                workload=ref, state=RunState.RUNNING, ctx=SYSTEM_CONTEXT
            )
        ),
    )
    app.state.trigger_runner = TriggerRunner(
        trigger_store,
        app.state.trigger_scheduler,
        app.state.trigger_engine,
        app.state.watch_runner,
    )
    provider = build_provider(settings)
    app.state.provider = provider
    eval_store = build_eval_store(settings)
    app.state.eval_store = eval_store
    app.state.rubric_registry = RubricRegistry(eval_store)
    app.state.eval_service = EvalService(
        eval_store,
        judge=RubricJudge(provider, model=settings.eval.judge_model, cost_table=cost_table),
        rubrics=app.state.rubric_registry,
        quality_target=settings.eval.quality_target,
    )

    def _eval_hook(run: Run) -> None:
        rubric = default_rubric()
        sample = app.state.eval_service.maybe_sample(
            run,
            sample_rate=settings.eval.sample_rate,
            rubric=rubric,
            pii_patterns=tuple(settings.eval.pii_patterns),
            cost_cap_usd=settings.eval.cost_cap_usd,
        )
        if sample is not None:
            app.state.eval_service.score(sample, run, rubric=rubric)

    def _on_production_completed(run: Run) -> None:
        """Feed a completed production run's outcome back into cost accounting."""
        if settings.eval.enabled:
            _eval_hook(run)
        run_ctx = context_for_run(run.tenant_id, run.team_id, run.attribution_key)
        app.state.cost_service.mark_run_completed(run.id, ctx=run_ctx)
        task_type = "default"
        if isinstance(run.task, dict):
            raw = run.task.get("type")
            if isinstance(raw, str) and raw:
                task_type = raw
        app.state.cost_estimator.record(run.workload_id, task_type, run.cost_usd)

    app.state.run_service.attach_eval_hook(_on_production_completed)
    router_store = build_router_store(settings)
    app.state.router_store = router_store
    app.state.router_engine = None
    if settings.router.enabled:
        app.state.router_engine = RouterEngine(
            RegistryCatalog(registry),
            LLMTaskClassifier(provider, model=settings.router.model),
            store=router_store,
            confidence_threshold=settings.router.confidence_threshold,
            margin=settings.router.margin,
            context=AdmissionContext(settings.router.context),
        )
    agent_tool_store = build_agent_tool_store(settings)
    agent_tool_registry = AgentToolRegistry(registry)
    app.state.agent_tool_store = agent_tool_store
    app.state.agent_tool_registry = agent_tool_registry
    app.state.agent_tool_invoker = AgentToolInvoker(
        agent_tool_registry,
        app.state.run_service,
        store=agent_tool_store,
    )
    app.state.a2a_adapter = None
    if settings.a2a.enabled:
        app.state.a2a_adapter = A2AAdapter(
            agent_tool_registry,
            app.state.run_service,
            router=app.state.router_engine,
            plane_id=settings.a2a.plane_id,
            allowed_planes=list(settings.a2a.allowed_planes),
            plane_secrets=dict(settings.a2a.plane_secrets),
        )
    if settings.model.provider == "fake" and settings.environment != "local":
        _LOGGER.warning(
            "model.provider is 'fake' in the %s environment: model calls return "
            "replayed or echoed content, not real inference",
            settings.environment,
        )
    if settings.model.provider in ("local", "cloud") and not settings.model.model_aliases:
        _LOGGER.warning(
            "model.provider is %r with empty model_aliases: server-reported model "
            "names must map to the bound canonical identity or every model call "
            "raises ModelIdentityMismatchError",
            settings.model.provider,
        )
    if settings.execution.adapter == "none":
        _LOGGER.warning(
            "execution.adapter is 'none': runs are admitted but never executed; "
            "set HIVEPLANE_EXECUTION__ADAPTER=raw-worker (or langgraph) to execute"
        )
    if settings.certification.executor == "none":
        _LOGGER.warning(
            "certification.executor is 'none': certification is refused; set "
            "HIVEPLANE_CERTIFICATION__EXECUTOR=adapter (real agent) or reference"
        )
    if settings.execution.adapter == "raw-worker":
        app.state.adapter = attach_raw_worker(
            app.state.run_service,
            app.state.tool_gateway,
            provider=provider,
            cost_table=cost_table,
            sandbox_channel=app.state.sandbox_channel,
            base_url=settings.execution.base_url,
            subprocess_spawner=SubprocessSpawner(),
            sandbox_mode=settings.execution.sandbox_mode,
        )
    elif settings.execution.adapter == "langgraph":
        app.state.adapter = attach_langgraph(
            app.state.run_service,
            app.state.tool_gateway,
            provider=provider,
            cost_table=cost_table,
        )
    elif settings.execution.adapter == "pydanticai":
        app.state.adapter = attach_pydanticai(
            app.state.run_service,
            app.state.tool_gateway,
            provider=provider,
            cost_table=cost_table,
        )
    elif settings.execution.adapter == "openai-agents":
        app.state.adapter = attach_openai_agents(
            app.state.run_service,
            app.state.tool_gateway,
            provider=provider,
            cost_table=cost_table,
        )
    elif settings.execution.adapter == "auto":
        app.state.adapter = attach_auto_adapters(
            app.state.run_service,
            app.state.tool_gateway,
            provider=provider,
            cost_table=cost_table,
            sandbox_channel=app.state.sandbox_channel,
            base_url=settings.execution.base_url,
            subprocess_spawner=SubprocessSpawner(),
            sandbox_mode=settings.execution.sandbox_mode,
        )
        app.state.adapters = app.state.adapter.adapters
    else:
        app.state.adapter = None
    app.state.adapter_catalog = _adapter_catalog(app.state.adapter, settings)
    if certification_coordinator is None and registry_service is None:
        certification_coordinator = _build_certification_coordinator(
            registry,
            private_key,
            app.state.run_service,
            settings,
            approval_service,
            transparency_log,
            app.state.corpus_version_service,
        )
    app.state.certification_coordinator = certification_coordinator
    app.state.certification_store = getattr(certification_coordinator, "store", None)
    app.state.audit_log = build_audit_log()
    app.state.audit_export_service = AuditExportService(
        app.state.reporting_store,
        app.state.audit_log,
        audit=app.state.audit_log,
    )
    if settings.reporting.signing_key_file:
        reporting_key, _ = load_or_generate_keypair(settings.reporting.signing_key_file)
    else:
        reporting_key, _ = generate_keypair()
    if app.state.certification_store is not None:
        app.state.evidence_service = EvidencePackService(
            app.state.reporting_store,
            app.state.approval_service,
            app.state.certification_store,
            app.state.cost_store,
            private_key=reporting_key,
            key_id=settings.reporting.signing_key_id,
        )
        app.state.evidence_service.bind_audit(app.state.audit_log)
    if settings.defense.enabled:
        app.state.defense_guard.bind_audit(app.state.audit_log)
    app.state.kill_switch.bind_audit(app.state.audit_log)
    app.state.incident_service.bind_audit(app.state.audit_log)
    app.state.artifact_service.bind_audit(app.state.audit_log)
    app.state.retention_service.bind_audit(app.state.audit_log)
    app.state.fleet_event_service.bind_audit(app.state.audit_log)
    app.state.escalation_service = _build_escalation_service(settings, app)
    app.state.retention_enforcer = RetentionEnforcer(
        app.state.reporting_store,
        app.state.retention_service,
        app.state.run_store,
        app.state.audit_log,
        app.state.cost_store,
        app.state.auth_store,
        app.state.delivery_store,
    )
    app.state.tenant_purge_service = TenantPurgeService(
        app.state.reporting_store,
        [
            PurgeTarget("artifacts", app.state.artifact_service.purge_tenant),
            PurgeTarget(
                "runs",
                lambda tenant_id: app.state.run_store.purge_tenant(tenant_id, ctx=SYSTEM_CONTEXT),
            ),
            PurgeTarget("cost", app.state.cost_store.purge_tenant),
            PurgeTarget(
                "auth",
                lambda tenant_id: app.state.auth_store.purge_tenant(tenant_id, ctx=SYSTEM_CONTEXT),
            ),
            PurgeTarget("delivery", app.state.delivery_store.purge_tenant),
            PurgeTarget("reports", app.state.reporting_store.purge_tenant_reports),
            PurgeTarget(
                "tenancy",
                lambda tenant_id: app.state.tenant_store.purge_tenant(SYSTEM_CONTEXT, tenant_id),
            ),
            PurgeTarget("audit", lambda _tenant_id: 0),
            PurgeTarget("eval", lambda _tenant_id: 0),
            PurgeTarget("feedback", lambda _tenant_id: 0),
            PurgeTarget("progressive", lambda _tenant_id: 0),
            PurgeTarget("reconcile", lambda _tenant_id: 0),
        ],
        legal_hold_check=lambda tenant_id: any(
            policy.legal_hold
            for policy in app.state.retention_service.policies(tenant_id, ctx=SYSTEM_CONTEXT)
        ),
        signing_key=reporting_key,
        key_id=settings.reporting.signing_key_id,
    )
    app.state.tenant_purge_service.bind_audit(app.state.audit_log)
    app.state.promotion_store = build_promotion_store(settings)
    app.state.promotion_gate = PromotionGate(
        registry,
        coordinator=certification_coordinator,
        store=app.state.promotion_store,
        audit=app.state.audit_log,
        on_promoted=lambda workload, version: app.state.result_cache.invalidate_workload(
            workload, current_version=version
        ),
    )
    progressive_store = build_progressive_store(settings)
    app.state.progressive_store = progressive_store
    app.state.shadow_service = ShadowService(
        progressive_store, runner=RunShadowRunner(app.state.run_service)
    )
    app.state.canary_service = CanaryService(
        progressive_store, registry=registry, audit=app.state.audit_log
    )
    app.state.run_service.attach_canary(app.state.canary_service)
    app.state.experiment_service = ExperimentService(progressive_store)
    _wire_health(app, registry, settings)
    app.state.plane_metrics = PlaneMetrics()
    app.state.plane_metrics.set_gauge("hiveplane_up", 1)
    app.state.probe_service = ProbeService(
        runner=_RunServiceProbeRunner(app.state.run_service),
        budget_cap_usd=settings.probes.budget_cap_usd,
    )
    if settings.drift.enabled and certification_coordinator is not None:
        _wire_drift(app, registry, certification_coordinator, settings)
    drift_store = getattr(app.state, "drift_store", None)
    if drift_store is not None:
        app.state.digest_service = DigestService(
            app.state.reporting_store,
            app.state.cost_service,
            drift_store,
            app.state.approval_service,
            registry,
            audit=app.state.audit_log,
        )
        app.state.digest_scheduler = DigestScheduler(
            app.state.reporting_store,
            app.state.digest_service,
            app.state.delivery_service,
            audit=app.state.audit_log,
        )
    app.state.run_recovery = RunRecovery(app.state.run_service)
    readiness_engine = (
        create_engine_from_settings(settings) if settings.execution.store == "postgres" else None
    )
    app.state.readiness = build_readiness_probe(
        engine=readiness_engine,
        get_adapter=lambda: app.state.adapter,
        get_certification_store=lambda: app.state.certification_store,
        get_public_key=lambda: app.state.attestation_public_key,
    )

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Liveness probe: the process is up."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    def readyz(response: Response) -> dict[str, Any]:
        """Readiness probe: every control-plane dependency is live."""
        report = app.state.readiness.check()
        if not report.ready:
            response.status_code = 503
            return {
                "status": "not_ready",
                "reasons": [check.reason for check in report.checks if not check.ok],
            }
        return {"status": "ready"}

    @app.get("/manifest/schema", tags=["manifest"])
    def manifest_schema() -> dict[str, Any]:
        """Return the JSON Schema for the AgentWorkload manifest."""
        return manifest_json_schema()

    @app.exception_handler(WorkloadNotFoundError)
    async def _workload_not_found(request: Request, exc: WorkloadNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(WorkloadAlreadyExistsError)
    async def _workload_exists(request: Request, exc: WorkloadAlreadyExistsError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    def _make_handler(error_type: type[Exception], status_code: int) -> Any:
        async def _handler(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})

        return _handler

    for _error_type, _status_code in _ERROR_STATUS:
        app.add_exception_handler(_error_type, _make_handler(_error_type, _status_code))

    for _run_error, _run_status in (
        (RunNotFoundError, 404),
        (IllegalTransitionError, 409),
        (RunNotIntervenableError, 409),
        (RunAdmissionRefusedError, 403),
    ):
        app.add_exception_handler(_run_error, _make_handler(_run_error, _run_status))

    for _learning_error, _learning_status in (
        (FeedbackNotFoundError, 404),
        (FeedbackNotAllowedError, 409),
        (CandidateNotFoundError, 404),
        (CandidateAlreadyReviewedError, 409),
        (CandidateAlreadyExistsError, 409),
        (CandidateNotAllowedError, 409),
    ):
        app.add_exception_handler(_learning_error, _make_handler(_learning_error, _learning_status))

    for _progressive_error, _progressive_status in (
        (ShadowNotFoundError, 404),
        (ShadowBudgetExceededError, 409),
        (CanaryNotFoundError, 404),
        (CanaryNotAllowedError, 409),
        (ExperimentNotFoundError, 404),
    ):
        app.add_exception_handler(
            _progressive_error, _make_handler(_progressive_error, _progressive_status)
        )

    app.add_exception_handler(ReplayNotFoundError, _make_handler(ReplayNotFoundError, 404))

    for _policy_error, _policy_status in (
        (ApprovalNotFoundError, 404),
        (PolicyPackNotFoundError, 404),
        (PolicyPackAlreadyExistsError, 409),
        (ApprovalAlreadyDecidedError, 409),
        (UnknownModelPriceError, 422),
        (MissingModelIdentityError, 422),
    ):
        app.add_exception_handler(_policy_error, _make_handler(_policy_error, _policy_status))

    app.include_router(registry_router)
    app.include_router(backup_router)
    app.include_router(demo_router)
    app.include_router(federation_router)
    app.include_router(replay_router)
    app.include_router(reporting_router)
    app.include_router(artifacts_router)
    app.include_router(runs_router)
    app.include_router(policy_router)
    app.include_router(approvals_router)
    app.include_router(certifications_router)
    app.include_router(promotions_router)
    app.include_router(progressive_router)
    app.include_router(drift_router)
    app.include_router(fleet_router)
    app.include_router(sandbox_router)
    app.include_router(security_router)
    app.include_router(spend_router)
    app.include_router(transparency_router)
    app.include_router(reconcile_router)
    app.include_router(triggers_router)
    app.include_router(pipelines_router)
    app.include_router(learning_router)
    app.include_router(health_router)
    app.include_router(mcp_router)
    app.include_router(identity_router)
    app.include_router(tenants_router)
    app.include_router(workers_router)
    app.include_router(scheduler_router)
    app.include_router(search_router)
    app.include_router(cluster_router)
    app.include_router(cost_router)
    app.include_router(corpora_router)
    app.include_router(events_router)
    app.include_router(services_router)
    app.include_router(v2_router)
    app.include_router(delivery_router)
    app.include_router(route_router)
    app.include_router(agent_tools_router)
    app.include_router(ask_router)
    app.include_router(adapters_router)
    if settings.a2a.enabled:
        app.include_router(a2a_router)
    return app


def _wire_drift(
    app: FastAPI,
    registry: RegistryService,
    coordinator: CertificationCoordinator,
    settings: Settings,
) -> None:
    """Install the drift detector, quarantine, notification, and reinstatement (M34)."""
    drift = settings.drift
    store = build_drift_store(settings)
    scheduler = DriftScheduler(
        registry,
        default_interval=settings.certification.re_cert_interval_days * 86400,
        renewal_window=drift.renewal_window_days * 86400,
        clock=registry.clock,
    )
    detector = DriftDetector(
        threshold_pass_rate=settings.certification.drift_threshold_pass_rate,
        max_new_failures=settings.certification.max_new_failures,
        required_consecutive_failures=drift.required_consecutive_failures,
        strong_multiplier=drift.strong_multiplier,
    )
    quarantine_service = QuarantineService(
        registry,
        store,
        audit=app.state.audit_log,
        notifier=_build_drift_notifier(settings),
        cancel_in_flight=drift.cancel_in_flight,
    )
    app.state.drift_store = store
    app.state.drift_scheduler = scheduler
    fleet_events = getattr(app.state, "fleet_event_service", None)
    if fleet_events is not None:
        quarantine_service.bind_event_sink(fleet_events.publish)
    app.state.drift_monitor = DriftMonitor(
        detector, store, coordinator, quarantine_service=quarantine_service
    )
    app.state.quarantine_service = quarantine_service
    app.state.reinstatement_service = ReinstatementService(
        registry, store, coordinator, audit=app.state.audit_log
    )


def _wire_health(app: FastAPI, registry: RegistryService, settings: Settings) -> None:
    """Install the agent health service with real event lookups (M42)."""

    def _slo(workload: str, *, ctx: TenantContext) -> SloTarget:
        slo = registry.get(workload, ctx=ctx).manifest.spec.health.slo
        return SloTarget(
            availability_target=slo.availability_target,
            quality_target=slo.quality_target,
            window_seconds=int(slo.error_budget_window),
        )

    def _drift(workload: str, *, ctx: TenantContext) -> str:
        store = getattr(app.state, "drift_store", None)
        if store is not None and store.active_quarantine(workload) is not None:
            return "detected"
        return "clean"

    def _breaker(workload: str, *, ctx: TenantContext) -> bool:
        breakers: CircuitBreakerRegistry = app.state.circuit_breakers
        snapshot = breakers.snapshot("workload", workload)
        return snapshot.state.value == "open"

    def _quality(workload: str, *, ctx: TenantContext) -> float | None:
        quality = app.state.eval_service.quality(workload, ctx=ctx)
        return quality.mean_score if quality.sample_count > 0 else None

    def _quarantine(workload: str, reason: str, *, ctx: TenantContext) -> None:
        # Go through the quarantine service so a durable QuarantineRecord exists and
        # the workload can be reinstated after re-certification (M61 fresh-boot/health
        # finding: registry.quarantine alone left an unreinstatable quarantine).
        service = getattr(app.state, "quarantine_service", None)
        if service is not None:
            service.quarantine(workload, reason=reason, actor="health", ctx=ctx)
        else:
            registry.quarantine(workload, ctx=ctx)

    def _throttle(workload: str, reason: str, *, ctx: TenantContext) -> None:
        app.state.throttled_workloads.add(workload)
        audit = getattr(app.state, "audit_log", None)
        if audit is not None:
            audit.append("health", "health.throttle", workload, detail=reason, ctx=ctx)

    app.state.health_service = HealthService(
        run_history=lambda workload, *, ctx, finished_after=None, states=None: (
            app.state.run_service.list_runs(
                workload=workload,
                ctx=ctx,
                finished_after=finished_after,
                states=states,
            )
        ),
        quality_lookup=_quality,
        drift_lookup=_drift,
        breaker_lookup=_breaker,
        status_lookup=lambda workload, *, ctx: (
            registry.get(workload, ctx=ctx).certification_status.value
        ),
        slo_lookup=_slo,
        workload_lookup=lambda *, ctx: [record.name for record in registry.list_workloads(ctx=ctx)],
        window_seconds=settings.health.window_seconds,
        min_runs_for_score=settings.health.min_runs_for_score,
        quarantine=_quarantine,
        throttle=_throttle,
    )


def _build_drift_notifier(settings: Settings) -> DriftNotifier:
    """Build the owner notifier from drift/fan-out webhook settings (M34-04)."""
    drift = settings.drift
    slack_url = drift.slack_webhook_url or settings.fanout.slack_webhook_url
    webhook_url = drift.generic_webhook_url or settings.fanout.generic_webhook_url
    transports: dict[FanOutType, DeliveryTransport] = {}
    destinations: list[FanOutDestination] = []
    if slack_url:
        transports[FanOutType.SLACK] = SlackTransport(webhook_url=slack_url)
        destinations.append(FanOutDestination(type=FanOutType.SLACK, channel=drift.slack_channel))
    if webhook_url:
        transports[FanOutType.WEBHOOK] = WebhookTransport(default_url=webhook_url)
        destinations.append(FanOutDestination(type=FanOutType.WEBHOOK, url=webhook_url))
    return DriftNotifier(transports, destinations, enabled=drift.notify)


def _build_incident_broadcaster(settings: Settings) -> IncidentBroadcaster:
    """Build the incident owner broadcaster from fan-out settings (M53)."""
    slack_url = settings.fanout.slack_webhook_url
    webhook_url = settings.fanout.generic_webhook_url
    transports: dict[FanOutType, DeliveryTransport] = {}
    destinations: list[FanOutDestination] = []
    if slack_url:
        transports[FanOutType.SLACK] = SlackTransport(webhook_url=slack_url)
        destinations.append(FanOutDestination(type=FanOutType.SLACK, channel="#ops"))
    if webhook_url:
        transports[FanOutType.WEBHOOK] = WebhookTransport(default_url=webhook_url)
        destinations.append(FanOutDestination(type=FanOutType.WEBHOOK, url=webhook_url))
    return IncidentBroadcaster(transports, destinations, enabled=settings.fanout.enabled)


def _build_certification_coordinator(
    registry: RegistryService,
    private_key: Ed25519PrivateKey,
    run_service: RunService,
    settings: Settings,
    approvals: ApprovalService | None = None,
    transparency_log: TransparencyLog | None = None,
    corpus_version_service: CorpusVersionService | None = None,
) -> CertificationCoordinator:
    """Build the default certification coordinator from settings."""
    cert = settings.certification
    policy = CertificationPolicy(
        staging=Thresholds(**cert.staging.model_dump()),
        production=Thresholds(**cert.production.model_dump()),
        re_cert_interval=cert.re_cert_interval_days * 86400,
    )
    environment = Environment(
        sandbox_image="hiveplane/sandbox:0.1.0",
        runtime_adapter="control-plane",
        control_plane_version=__version__,
    )
    service = CertificationService(
        CertificationEngine(policy),
        registry,
        private_key=private_key,
        environment=environment,
        key_id=cert.signing_key_id,
        transparency_log=transparency_log,
    )
    executor, executor_factory = _select_task_executor(settings, run_service, registry, approvals)
    return CertificationCoordinator(
        registry,
        service,
        build_certification_store(settings),
        executor=executor,
        executor_factory=executor_factory,
        corpora_dir=cert.corpora_dir,
        environment=environment,
        corpus_integrator=(
            corpus_version_service.integrate if corpus_version_service is not None else None
        ),
    )


def _select_task_executor(
    settings: Settings,
    run_service: RunService,
    registry: RegistryService,
    approvals: ApprovalService | None = None,
) -> tuple[TaskExecutor, Callable[[str, str | None], TaskExecutor] | None]:
    """Choose the certification task executor.

    The ``adapter`` executor needs the workload and pinned model identity, which
    are only known when a certification runs, so it is returned as a factory
    rather than a single instance (M23, #109).
    """
    if settings.certification.executor == "reference":
        return ReferenceExecutor(), None
    if settings.certification.executor == "adapter":
        factory = _adapter_executor_factory()
        if factory is not None:

            def _build(workload: str, model_identity: str | None) -> TaskExecutor:
                return cast(
                    "TaskExecutor",
                    factory(
                        settings,
                        run_service,
                        registry,
                        workload=workload,
                        model_identity=model_identity,
                        approvals=approvals,
                    ),
                )

            return UnconfiguredTaskExecutor(), _build
        _LOGGER.warning(
            "certification executor 'adapter' is configured but "
            "hiveplane.certification.executor.build_task_executor is unavailable; "
            "falling back to the unconfigured executor"
        )
    return UnconfiguredTaskExecutor(), None


def _adapter_catalog(adapter: Any | None, settings: Settings) -> dict[str, Any]:
    """Return the configured adapters keyed by adapter name for introspection."""
    if adapter is None:
        return {}
    adapters = getattr(adapter, "adapters", None)
    if adapters is not None:
        return {runtime.value: bound for runtime, bound in adapters.items()}
    return {settings.execution.adapter: adapter}


def _workload_io(registry: RegistryService, workload: str) -> IOSpec | None:
    """Return a workload's declared handoff schemas, or None when unregistered."""
    try:
        return registry.get(workload).manifest.spec.io
    except WorkloadNotFoundError:
        return None


def _workload_budget(registry: RegistryService, workload: str) -> float | None:
    """Return a workload's per-run budget, or None when unregistered."""
    try:
        return registry.get(workload).manifest.spec.budget.per_run_usd
    except WorkloadNotFoundError:
        return None


def _adapter_executor_factory() -> Any | None:
    """Return the adapter executor factory if the optional module is present."""
    try:
        module = import_module("hiveplane.certification.executor")
    except ImportError:
        return None
    return getattr(module, "build_task_executor", None)


app = create_app()
