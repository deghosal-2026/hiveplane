"""FastAPI dependencies for the registry API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from hiveplane.a2a import A2AAdapter
from hiveplane.adapters.base import Adapter
from hiveplane.agent_tools.engine import AgentToolInvoker
from hiveplane.agent_tools.registry import AgentToolRegistry
from hiveplane.agent_tools.store import AgentToolStore
from hiveplane.auth.models import (
    AuthenticationError,
    AuthMethod,
    AuthorizationError,
    OperatorIdentity,
    Permission,
)
from hiveplane.auth.service import AuthService
from hiveplane.budget.store import BudgetStore
from hiveplane.certification.promotion import PromotionGate
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.defense.events import SecurityEventStore
from hiveplane.drift.monitor import DriftMonitor
from hiveplane.drift.quarantine import QuarantineService
from hiveplane.drift.reinstatement import ReinstatementService
from hiveplane.drift.scheduler import DriftScheduler
from hiveplane.execution.service import RunService
from hiveplane.execution.tools import ToolGateway
from hiveplane.health.plane_metrics import PlaneMetrics
from hiveplane.health.service import HealthService
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.eval import EvalService
from hiveplane.learning.feedback import FeedbackService
from hiveplane.mcp.registry import McpRegistry
from hiveplane.pipelines.engine import PipelineEngine
from hiveplane.pipelines.store import PipelineStore
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.kill_switch import KillSwitch
from hiveplane.policy.pack_registry import PolicyPackRegistry
from hiveplane.policy.packs import PolicyPackStore
from hiveplane.probes.service import ProbeService
from hiveplane.progressive.canary import CanaryService
from hiveplane.progressive.experiments import ExperimentService
from hiveplane.progressive.shadow import ShadowService
from hiveplane.reconcile.controller import ReconcileController
from hiveplane.reconcile.store import ReconcileStore
from hiveplane.registry.service import RegistryService
from hiveplane.router.engine import RouterEngine
from hiveplane.router.store import RouterStore
from hiveplane.secrets.service import SecretService
from hiveplane.tenancy import Role, TenantContext
from hiveplane.tenancy.context import DEFAULT_CONTEXT
from hiveplane.transparency.verify import PublicVerifier
from hiveplane.triggers.engine import TriggerEngine
from hiveplane.triggers.freeze import FreezeService
from hiveplane.triggers.ingest import WebhookVerifier
from hiveplane.triggers.store import TriggerStore

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


def get_policy_pack_registry(request: Request) -> PolicyPackRegistry:
    """Return the policy pack registry bound to the application state."""
    registry: PolicyPackRegistry = request.app.state.policy_pack_registry
    return registry


def get_auth_service(request: Request) -> AuthService:
    """Return the auth service bound to the application state."""
    service: AuthService = request.app.state.auth_service
    return service


def get_secret_service(request: Request) -> SecretService:
    """Return the secret service bound to the application state."""
    service: SecretService = request.app.state.secret_service
    return service


def get_principal(request: Request) -> OperatorIdentity:
    """Resolve the caller's identity; anonymous admin when auth is disabled."""
    from fastapi import HTTPException, status

    from hiveplane.config import get_settings
    from hiveplane.tenancy.models import Role

    settings = get_settings()
    if not settings.auth.enabled:
        return OperatorIdentity(
            operator_id="anonymous",
            tenant_id="default",
            role=Role.ADMIN,
            method=AuthMethod.SESSION,
        )
    service: AuthService = request.app.state.auth_service
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer key")
    try:
        return service.authenticate_key(header[len("Bearer ") :])
    except AuthenticationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


def require_permission(
    permission: Permission,
) -> Callable[..., OperatorIdentity]:
    """Build a dependency that authorizes ``permission`` server-side."""

    def _dependency(
        request: Request,
        identity: Annotated[OperatorIdentity, Depends(get_principal)],
    ) -> OperatorIdentity:
        from fastapi import HTTPException, status

        service: AuthService = request.app.state.auth_service
        try:
            service.authorize(identity, permission)
        except AuthorizationError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        return identity

    return _dependency


def get_mcp_registry(request: Request) -> McpRegistry:
    """Return the live MCP registry bound to the application state."""
    registry: McpRegistry = request.app.state.mcp_registry
    return registry


def get_health_service(request: Request) -> HealthService:
    """Return the agent health service bound to the application state."""
    service: HealthService = request.app.state.health_service
    return service


def get_probe_service(request: Request) -> ProbeService:
    """Return the synthetic probe service bound to the application state."""
    service: ProbeService = request.app.state.probe_service
    return service


def get_plane_metrics(request: Request) -> PlaneMetrics:
    """Return the plane self-metrics registry bound to the application state."""
    metrics: PlaneMetrics = request.app.state.plane_metrics
    return metrics


def get_kill_switch(request: Request) -> KillSwitch:
    """Return the tool kill switch bound to the application state."""
    switch: KillSwitch = request.app.state.kill_switch
    return switch


def get_approval_service(request: Request) -> ApprovalService:
    """Return the approval service bound to the application state."""
    service: ApprovalService = request.app.state.approval_service
    return service


def get_security_event_store(request: Request) -> SecurityEventStore:
    """Return the security-event store bound to the application state."""
    store: SecurityEventStore = request.app.state.security_event_store
    return store


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


def get_reconcile_controller(request: Request) -> ReconcileController:
    """Return the reconcile controller bound to the application state."""
    controller: ReconcileController = request.app.state.reconcile_controller
    return controller


def get_reconcile_store(request: Request) -> ReconcileStore:
    """Return the reconcile store bound to the application state."""
    store: ReconcileStore = request.app.state.reconcile_store
    return store


def get_trigger_store(request: Request) -> TriggerStore:
    """Return the trigger store bound to the application state."""
    store: TriggerStore = request.app.state.trigger_store
    return store


def get_trigger_engine(request: Request) -> TriggerEngine:
    """Return the trigger engine bound to the application state."""
    engine: TriggerEngine = request.app.state.trigger_engine
    return engine


def get_webhook_verifier(request: Request) -> WebhookVerifier:
    """Return the webhook verifier bound to the application state."""
    verifier: WebhookVerifier = request.app.state.webhook_verifier
    return verifier


def get_trigger_secrets(request: Request) -> dict[str, str]:
    """Return the configured trigger-id to webhook-secret map."""
    secrets: dict[str, str] = request.app.state.trigger_secrets
    return secrets


def get_freeze_service(request: Request) -> FreezeService:
    """Return the freeze service bound to the application state."""
    service: FreezeService = request.app.state.freeze_service
    return service


def get_pipeline_store(request: Request) -> PipelineStore:
    """Return the pipeline store bound to the application state."""
    store: PipelineStore = request.app.state.pipeline_store
    return store


def get_pipeline_engine(request: Request) -> PipelineEngine:
    """Return the pipeline engine bound to the application state."""
    engine: PipelineEngine = request.app.state.pipeline_engine
    return engine


def get_router_engine(request: Request) -> RouterEngine:
    """Return the smart task router bound to the application state."""
    engine: RouterEngine | None = getattr(request.app.state, "router_engine", None)
    if engine is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "router is not enabled; set HIVEPLANE_ROUTER__ENABLED=true",
        )
    return engine


def get_router_store(request: Request) -> RouterStore:
    """Return the router-decision store bound to the application state."""
    store: RouterStore = request.app.state.router_store
    return store


def get_agent_tool_registry(request: Request) -> AgentToolRegistry:
    """Return the agent-tool registry bound to the application state."""
    registry: AgentToolRegistry = request.app.state.agent_tool_registry
    return registry


def get_agent_tool_invoker(request: Request) -> AgentToolInvoker:
    """Return the agent-tool invoker bound to the application state."""
    invoker: AgentToolInvoker = request.app.state.agent_tool_invoker
    return invoker


def get_agent_tool_store(request: Request) -> AgentToolStore:
    """Return the agent-tool store bound to the application state."""
    store: AgentToolStore = request.app.state.agent_tool_store
    return store


def get_a2a_adapter(request: Request) -> A2AAdapter:
    """Return the A2A adapter, or 503 when A2A is disabled."""
    adapter: A2AAdapter | None = getattr(request.app.state, "a2a_adapter", None)
    if adapter is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "A2A is not enabled; set HIVEPLANE_A2A__ENABLED=true",
        )
    return adapter


def get_adapter_catalog(request: Request) -> dict[str, Adapter]:
    """Return the configured runtime adapters keyed by name."""
    catalog: dict[str, Adapter] = getattr(request.app.state, "adapter_catalog", {})
    return catalog


def get_public_verifier(request: Request) -> PublicVerifier:
    """Return the public attestation verifier bound to application state."""
    verifier: PublicVerifier | None = getattr(
        request.app.state, "public_verifier", None
    )
    if verifier is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "public verifier is not available"
        )
    return verifier


def get_feedback_service(request: Request) -> FeedbackService:
    """Return the run-feedback service bound to the application state."""
    service: FeedbackService = request.app.state.feedback_service
    return service


def get_candidate_service(request: Request) -> CandidateService:
    """Return the corpus-candidate service bound to the application state."""
    service: CandidateService = request.app.state.candidate_service
    return service


def get_eval_service(request: Request) -> EvalService:
    """Return the online-eval service bound to the application state."""
    service: EvalService = request.app.state.eval_service
    return service


def get_shadow_service(request: Request) -> ShadowService:
    """Return the shadow-run service bound to the application state."""
    service: ShadowService = request.app.state.shadow_service
    return service


def get_canary_service(request: Request) -> CanaryService:
    """Return the canary service bound to the application state."""
    service: CanaryService = request.app.state.canary_service
    return service


def get_experiment_service(request: Request) -> ExperimentService:
    """Return the experiment service bound to the application state."""
    service: ExperimentService = request.app.state.experiment_service
    return service


def get_promotion_gate(request: Request) -> PromotionGate:
    """Return the promotion gate bound to the application state."""
    gate: PromotionGate = request.app.state.promotion_gate
    return gate


def get_drift_scheduler(request: Request) -> DriftScheduler:
    """Return the drift scheduler, or 503 when drift detection is disabled."""
    scheduler: DriftScheduler | None = getattr(request.app.state, "drift_scheduler", None)
    if scheduler is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "drift detection is not enabled; set HIVEPLANE_DRIFT__ENABLED=true",
        )
    return scheduler


def get_drift_monitor(request: Request) -> DriftMonitor:
    """Return the drift monitor, or 503 when drift detection is disabled."""
    monitor: DriftMonitor | None = getattr(request.app.state, "drift_monitor", None)
    if monitor is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "drift detection is not enabled; set HIVEPLANE_DRIFT__ENABLED=true",
        )
    return monitor


def get_quarantine_service(request: Request) -> QuarantineService:
    """Return the quarantine service, or 503 when drift detection is disabled."""
    service: QuarantineService | None = getattr(
        request.app.state, "quarantine_service", None
    )
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "quarantine is not enabled; set HIVEPLANE_DRIFT__ENABLED=true",
        )
    return service


def get_reinstatement_service(request: Request) -> ReinstatementService:
    """Return the reinstatement service, or 503 when drift detection is disabled."""
    service: ReinstatementService | None = getattr(
        request.app.state, "reinstatement_service", None
    )
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "reinstatement is not enabled; set HIVEPLANE_DRIFT__ENABLED=true",
        )
    return service
