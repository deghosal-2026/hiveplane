"""FastAPI application factory for the HivePlane control plane.

M1 provides the health surface; M3-M4 add the registry API (workload CRUD,
fleet catalog, versioning, and dry-run). Execution, policy, and intervention
endpoints arrive in later milestones.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from hiveplane import __version__
from hiveplane.api.approvals import router as approvals_router
from hiveplane.api.policy import router as policy_router
from hiveplane.api.registry import router as registry_router
from hiveplane.api.runs import router as runs_router
from hiveplane.budget.errors import UnknownModelPriceError
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.manifest import manifest_json_schema
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.service import RunService
from hiveplane.execution.wiring import build_run_service
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
)


def create_app(
    registry_service: RegistryService | None = None,
    run_service: RunService | None = None,
) -> FastAPI:
    """Build and return the control-plane ASGI application."""
    app = FastAPI(
        title="HivePlane",
        version=__version__,
        summary="Control plane for production agent fleets.",
    )
    registry = registry_service or RegistryService(InMemoryRegistryStore())
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
    ):
        app.add_exception_handler(_policy_error, _make_handler(_policy_error, _policy_status))

    app.include_router(registry_router)
    app.include_router(runs_router)
    app.include_router(policy_router)
    app.include_router(approvals_router)
    return app


app = create_app()
