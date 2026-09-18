"""FastAPI application factory for the HivePlane control plane.

M1 provides the health surface; M3-M4 add the registry API (workload CRUD,
fleet catalog, versioning, and dry-run). Execution, policy, and intervention
endpoints arrive in later milestones.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from hiveplane import __version__, telemetry
from hiveplane.api.approvals import router as approvals_router
from hiveplane.api.certifications import router as certifications_router
from hiveplane.api.policy import router as policy_router
from hiveplane.api.registry import router as registry_router
from hiveplane.api.runs import router as runs_router
from hiveplane.budget.errors import MissingModelIdentityError, UnknownModelPriceError
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.errors import (
    CertificationNotFoundError,
    CorpusError,
    ExecutorNotConfiguredError,
)
from hiveplane.certification.models import CertificationPolicy, Environment, Thresholds
from hiveplane.certification.runner import ReferenceExecutor, UnconfiguredTaskExecutor
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.config import get_settings
from hiveplane.core.manifest import manifest_json_schema
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.service import RunService
from hiveplane.execution.wiring import attach_raw_worker, build_run_service, build_tool_gateway
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    PolicyPackAlreadyExistsError,
    PolicyPackNotFoundError,
)
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.policy.store import InMemoryApprovalStore
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
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.sandbox.manager import InMemorySandboxManager

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
    """Install the OpenTelemetry pipeline and flush signals on shutdown."""
    provider = telemetry.configure_telemetry(get_settings().otel)
    meter_provider = telemetry.configure_metrics(get_settings().otel)
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
    private_key, public_key = generate_keypair()
    registry = registry_service or RegistryService(
        InMemoryRegistryStore(), attestation_public_key=public_key
    )
    app.state.registry_service = registry
    policy_pack_store = InMemoryPolicyPackStore()
    policy_engine = PolicyEngine(policy_pack_store)
    approval_service = ApprovalService(InMemoryApprovalStore())
    budget_service = BudgetService(InMemoryBudgetStore(), CostTable())
    sandbox_manager = InMemorySandboxManager()
    app.state.policy_pack_store = policy_pack_store
    app.state.policy_engine = policy_engine
    app.state.approval_service = approval_service
    app.state.budget_service = budget_service
    app.state.sandbox_manager = sandbox_manager
    app.state.run_service = run_service or build_run_service(
        registry, policy_engine, approval_service, budget_service, sandbox_manager
    )
    app.state.tool_gateway = build_tool_gateway(
        registry, policy_engine, app.state.run_service, approval_service
    )
    app.state.adapter = (
        attach_raw_worker(app.state.run_service, app.state.tool_gateway)
        if get_settings().execution.adapter == "raw-worker"
        else None
    )
    if certification_coordinator is None and registry_service is None:
        certification_coordinator = _build_certification_coordinator(registry, private_key)
    app.state.certification_coordinator = certification_coordinator

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Liveness probe: the process is up."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    def readyz() -> dict[str, str]:
        """Readiness probe: the control plane can accept traffic."""
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
    return app


def _build_certification_coordinator(
    registry: RegistryService, private_key: Ed25519PrivateKey
) -> CertificationCoordinator:
    """Build the default certification coordinator from settings."""
    settings = get_settings()
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
    executor: ReferenceExecutor | UnconfiguredTaskExecutor = (
        ReferenceExecutor() if cert.executor == "reference" else UnconfiguredTaskExecutor()
    )
    return CertificationCoordinator(
        registry,
        service,
        InMemoryCertificationStore(),
        executor=executor,
        corpora_dir=cert.corpora_dir,
        environment=environment,
    )


app = create_app()
