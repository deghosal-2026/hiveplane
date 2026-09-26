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
from hiveplane.api.approvals import router as approvals_router
from hiveplane.api.certifications import router as certifications_router
from hiveplane.api.policy import router as policy_router
from hiveplane.api.readiness import build_readiness_probe
from hiveplane.api.reconcile import router as reconcile_router
from hiveplane.api.registry import router as registry_router
from hiveplane.api.runs import router as runs_router
from hiveplane.api.sandbox_channel import SandboxChannel
from hiveplane.api.sandbox_channel import router as sandbox_router
from hiveplane.api.spend import router as spend_router
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
from hiveplane.core.manifest import manifest_json_schema
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.recovery import RunRecovery
from hiveplane.execution.service import RunService
from hiveplane.execution.subprocess_spawner import SubprocessSpawner
from hiveplane.execution.wiring import (
    attach_auto_adapters,
    attach_langgraph,
    attach_raw_worker,
    build_run_service,
    build_tool_gateway,
)
from hiveplane.llm.factory import build_provider
from hiveplane.persistence.base import create_engine_from_settings
from hiveplane.persistence.migrate import run_migrations
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
from hiveplane.sandbox.manager import InMemorySandboxManager

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
    registry = registry_service or RegistryService(
        build_registry_store(settings), attestation_public_key=public_key
    )
    app.state.registry_service = registry
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
    app.state.budget_store = budget_store
    app.state.budget_service = budget_service
    app.state.cost_table = cost_table
    app.state.sandbox_manager = sandbox_manager
    app.state.sandbox_channel = SandboxChannel()
    app.state.run_service = run_service or build_run_service(
        registry, policy_engine, approval_service, budget_service, sandbox_manager
    )
    app.state.tool_gateway = build_tool_gateway(
        registry, policy_engine, app.state.run_service, approval_service
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
    provider = build_provider(settings)
    app.state.provider = provider
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
    if certification_coordinator is None and registry_service is None:
        certification_coordinator = _build_certification_coordinator(
            registry, private_key, app.state.run_service, settings, approval_service
        )
    app.state.certification_coordinator = certification_coordinator
    app.state.certification_store = getattr(certification_coordinator, "store", None)
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
    app.include_router(sandbox_router)
    app.include_router(spend_router)
    app.include_router(reconcile_router)
    return app


def _build_certification_coordinator(
    registry: RegistryService,
    private_key: Ed25519PrivateKey,
    run_service: RunService,
    settings: Settings,
    approvals: ApprovalService | None = None,
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


def _adapter_executor_factory() -> Any | None:
    """Return the adapter executor factory if the optional module is present."""
    try:
        module = import_module("hiveplane.certification.executor")
    except ImportError:
        return None
    return getattr(module, "build_task_executor", None)


app = create_app()
