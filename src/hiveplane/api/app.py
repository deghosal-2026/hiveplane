"""FastAPI application factory for the HivePlane control plane.

M1 provides the health surface; M3-M4 add the registry API (workload CRUD,
fleet catalog, versioning, and dry-run). Execution, policy, and intervention
endpoints arrive in later milestones.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
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
from hiveplane.api.approvals import router as approvals_router
from hiveplane.api.certifications import router as certifications_router
from hiveplane.api.drift import router as drift_router
from hiveplane.api.learning import router as learning_router
from hiveplane.api.pipelines import router as pipelines_router
from hiveplane.api.policy import router as policy_router
from hiveplane.api.progressive import router as progressive_router
from hiveplane.api.promotions import router as promotions_router
from hiveplane.api.readiness import build_readiness_probe
from hiveplane.api.reconcile import router as reconcile_router
from hiveplane.api.registry import router as registry_router
from hiveplane.api.router import router as route_router
from hiveplane.api.runs import router as runs_router
from hiveplane.api.sandbox_channel import SandboxChannel
from hiveplane.api.sandbox_channel import router as sandbox_router
from hiveplane.api.security import router as security_router
from hiveplane.api.spend import router as spend_router
from hiveplane.api.transparency import router as transparency_router
from hiveplane.api.triggers import router as triggers_router
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
from hiveplane.config import Settings, get_settings
from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.manifest import manifest_json_schema
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.core.spec import IOSpec
from hiveplane.defense.escalation import AttemptEscalator
from hiveplane.defense.events import build_security_event_store
from hiveplane.defense.guard import DefenseGuard
from hiveplane.defense.scanner import DefenseScanner, DetectorConfig, DetectorSeverity
from hiveplane.defense.taint import TaintRegistry
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
    build_run_service,
    build_tool_gateway,
)
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
from hiveplane.persistence.base import create_engine_from_settings
from hiveplane.persistence.migrate import run_migrations
from hiveplane.pipelines.engine import PipelineEngine
from hiveplane.pipelines.executor import RunNodeExecutor, ServiceApprovalGate
from hiveplane.pipelines.store import build_pipeline_store
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    PolicyPackAlreadyExistsError,
    PolicyPackNotFoundError,
)
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.policy.store import build_approval_store
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
    WorkloadNotFoundError,
)
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import build_registry_store
from hiveplane.router.catalog import RegistryCatalog
from hiveplane.router.classifier import LLMTaskClassifier
from hiveplane.router.engine import RouterEngine
from hiveplane.router.store import build_router_store
from hiveplane.sandbox.manager import InMemorySandboxManager
from hiveplane.tenancy import TenantContext
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
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import build_trigger_store

_LOGGER = logging.getLogger(__name__)

#: Registry errors mapped to HTTP status codes.
_ERROR_STATUS: tuple[tuple[type[Exception], int], ...] = (
    (WorkloadNotFoundError, 404),
    (VersionNotFoundError, 404),
    (AttestationNotFoundError, 404),
    (WorkloadAlreadyExistsError, 409),
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
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Migrate the system of record, then install the OpenTelemetry pipeline."""
    settings = get_settings()
    if settings.execution.store == "postgres":
        run_migrations(settings)
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
    yield
    provider.force_flush()
    meter_provider.force_flush()


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
    settings = get_settings()
    if settings.certification.signing_key_file:
        private_key, public_key = load_or_generate_keypair(
            settings.certification.signing_key_file
        )
    else:
        private_key, public_key = generate_keypair()
    app.state.attestation_public_key = public_key
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
    key_registry.add_key(settings.certification.signing_key_id, verifier_key)
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
    budget_service = BudgetService(budget_store, cost_table)
    sandbox_manager = InMemorySandboxManager()
    app.state.policy_pack_store = policy_pack_store
    app.state.policy_engine = policy_engine
    app.state.approval_service = approval_service
    app.state.security_event_store = build_security_event_store(settings)
    defense: DefenseGuard | None = None
    if settings.defense.enabled:
        escalator = AttemptEscalator(
            app.state.security_event_store,
            threshold=settings.defense.repeat_threshold,
            window_seconds=settings.defense.repeat_window_seconds,
            quarantine_provider=lambda: getattr(app.state, "quarantine_service", None),
        )
        defense = DefenseGuard(
            scanner=DefenseScanner(),
            events=app.state.security_event_store,
            taint=TaintRegistry(),
            escalator=escalator,
            config=DetectorConfig(
                severity_threshold=DetectorSeverity(
                    settings.defense.detector_severity_threshold
                ),
                escalate_to_block=settings.defense.escalate_to_block,
            ),
        )
        app.state.defense_guard = defense
    app.state.budget_store = budget_store
    app.state.budget_service = budget_service
    app.state.cost_table = cost_table
    app.state.sandbox_manager = sandbox_manager
    app.state.sandbox_channel = SandboxChannel()
    app.state.run_service = run_service or build_run_service(
        registry, policy_engine, approval_service, budget_service, sandbox_manager
    )
    app.state.tool_gateway = build_tool_gateway(
        registry, policy_engine, app.state.run_service, approval_service, defense
    )
    app.state.candidate_service = CandidateService(build_candidate_store(settings))
    app.state.corpus_version_service = CorpusVersionService(
        build_corpus_version_store(settings),
        candidates=app.state.candidate_service,
    )
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
            require_destructive_confirmation=(
                settings.reconcile.require_destructive_confirmation
            ),
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

    app.state.trigger_engine = TriggerEngine(
        trigger_store, trigger_limiter, app.state.run_service, freeze_check=_freeze_reason
    )
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
    provider = build_provider(settings)
    app.state.provider = provider
    eval_store = build_eval_store(settings)
    app.state.eval_store = eval_store
    app.state.rubric_registry = RubricRegistry(eval_store)
    app.state.eval_service = EvalService(
        eval_store,
        judge=RubricJudge(provider, model=settings.eval.judge_model),
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

    if settings.eval.enabled:
        app.state.run_service.attach_eval_hook(_eval_hook)
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
    if settings.defense.enabled:
        app.state.defense_guard.bind_audit(app.state.audit_log)
    app.state.promotion_store = build_promotion_store(settings)
    app.state.promotion_gate = PromotionGate(
        registry,
        coordinator=certification_coordinator,
        store=app.state.promotion_store,
        audit=app.state.audit_log,
    )
    progressive_store = build_progressive_store(settings)
    app.state.progressive_store = progressive_store
    app.state.shadow_service = ShadowService(
        progressive_store, runner=RunShadowRunner(app.state.run_service)
    )
    app.state.canary_service = CanaryService(
        progressive_store, registry=registry, audit=app.state.audit_log
    )
    app.state.experiment_service = ExperimentService(progressive_store)
    if settings.drift.enabled and certification_coordinator is not None:
        _wire_drift(app, registry, certification_coordinator, settings)
    app.state.run_recovery = RunRecovery(app.state.run_service)
    readiness_engine = (
        create_engine_from_settings(settings)
        if settings.execution.store == "postgres"
        else None
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
    async def _workload_not_found(
        request: Request, exc: WorkloadNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(WorkloadAlreadyExistsError)
    async def _workload_exists(
        request: Request, exc: WorkloadAlreadyExistsError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    def _make_handler(
        error_type: type[Exception], status_code: int
    ) -> Any:
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
    app.include_router(runs_router)
    app.include_router(policy_router)
    app.include_router(approvals_router)
    app.include_router(certifications_router)
    app.include_router(promotions_router)
    app.include_router(progressive_router)
    app.include_router(drift_router)
    app.include_router(sandbox_router)
    app.include_router(security_router)
    app.include_router(spend_router)
    app.include_router(transparency_router)
    app.include_router(reconcile_router)
    app.include_router(triggers_router)
    app.include_router(pipelines_router)
    app.include_router(learning_router)
    app.include_router(route_router)
    app.include_router(agent_tools_router)
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
    app.state.drift_monitor = DriftMonitor(
        detector, store, coordinator, quarantine_service=quarantine_service
    )
    app.state.quarantine_service = quarantine_service
    app.state.reinstatement_service = ReinstatementService(
        registry, store, coordinator, audit=app.state.audit_log
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
        destinations.append(
            FanOutDestination(type=FanOutType.SLACK, channel=drift.slack_channel)
        )
    if webhook_url:
        transports[FanOutType.WEBHOOK] = WebhookTransport(default_url=webhook_url)
        destinations.append(FanOutDestination(type=FanOutType.WEBHOOK, url=webhook_url))
    return DriftNotifier(transports, destinations, enabled=drift.notify)


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
    executor, executor_factory = _select_task_executor(
        settings, run_service, registry, approvals
    )
    return CertificationCoordinator(
        registry,
        service,
        build_certification_store(settings),
        executor=executor,
        executor_factory=executor_factory,
        corpora_dir=cert.corpora_dir,
        environment=environment,
        corpus_integrator=(
            corpus_version_service.integrate
            if corpus_version_service is not None
            else None
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

            def _build(
                workload: str, model_identity: str | None
            ) -> TaskExecutor:
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
