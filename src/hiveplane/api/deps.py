"""FastAPI dependencies for the registry API."""

from __future__ import annotations

from fastapi import Request

from hiveplane.budget.store import BudgetStore
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.execution.service import RunService
from hiveplane.execution.tools import ToolGateway
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import PolicyPackStore
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import Role, TenantContext
from hiveplane.tenancy.context import DEFAULT_CONTEXT

TENANT_HEADER = "X-Hiveplane-Tenant"
TEAM_HEADER = "X-Hiveplane-Team"


def get_tenant_context(request: Request) -> TenantContext:
    """Resolve the acting tenant from request headers.

    ``X-Hiveplane-Tenant`` selects the tenant and ``X-Hiveplane-Team`` the team;
    without headers the seeded default tenant acts. This is plumbing for
    multi-tenant data, not an authentication boundary — signed identities and
    API keys arrive in M45 (D33).
    """
    tenant_id = request.headers.get(TENANT_HEADER)
    if tenant_id is None:
        return DEFAULT_CONTEXT
    return TenantContext(
        tenant_id=tenant_id,
        team_id=request.headers.get(TEAM_HEADER),
        role=Role.ADMIN,
    )


def get_registry_service(request: Request) -> RegistryService:
    """Return the registry service bound to the application state."""
    service: RegistryService = request.app.state.registry_service
    return service


def get_run_service(request: Request) -> RunService:
    """Return the run service bound to the application state."""
    service: RunService = request.app.state.run_service
    return service


def get_policy_engine(request: Request) -> PolicyEngine:
    """Return the policy engine bound to the application state."""
    engine: PolicyEngine = request.app.state.policy_engine
    return engine


def get_policy_pack_store(request: Request) -> PolicyPackStore:
    """Return the policy pack store bound to the application state."""
    store: PolicyPackStore = request.app.state.policy_pack_store
    return store


def get_approval_service(request: Request) -> ApprovalService:
    """Return the approval service bound to the application state."""
    service: ApprovalService = request.app.state.approval_service
    return service


def get_budget_store(request: Request) -> BudgetStore:
    """Return the budget store bound to the application state."""
    store: BudgetStore = request.app.state.budget_store
    return store


def get_certification_coordinator(request: Request) -> CertificationCoordinator:
    """Return the certification coordinator bound to the application state."""
    coordinator: CertificationCoordinator = request.app.state.certification_coordinator
    return coordinator


def get_tool_gateway(request: Request) -> ToolGateway:
    """Return the tool-call boundary bound to the application state."""
    gateway: ToolGateway = request.app.state.tool_gateway
    return gateway
