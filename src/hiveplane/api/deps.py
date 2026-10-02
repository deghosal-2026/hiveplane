"""FastAPI dependencies for the registry API."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, status

from hiveplane.a2a import A2AAdapter
from hiveplane.adapters.base import Adapter
from hiveplane.agent_tools.engine import AgentToolInvoker
from hiveplane.agent_tools.registry import AgentToolRegistry
from hiveplane.agent_tools.store import AgentToolStore
from hiveplane.artifacts.service import ArtifactService, RetentionService
from hiveplane.ask.live import ServiceReaders
from hiveplane.ask.service import AskService
from hiveplane.auth.models import (
    AuthenticationError,
    AuthMethod,
    AuthorizationError,
    OperatorIdentity,
    Permission,
)
from hiveplane.auth.service import AuthService
from hiveplane.backup.service import BackupService
from hiveplane.budget.store import BudgetStore
from hiveplane.certification.promotion import PromotionGate
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.chaos.engine import ChaosEngine
from hiveplane.corpus.service import CorpusService
from hiveplane.cost.depth import CostEstimator, ResultCache
from hiveplane.cost.service import CostService
from hiveplane.defense.events import SecurityEventStore
from hiveplane.delivery.approvals import InteractiveApprovalService
from hiveplane.delivery.service import DeliveryService
from hiveplane.demo.seed import DemoSeeder
from hiveplane.drift.monitor import DriftMonitor
from hiveplane.drift.quarantine import QuarantineService
from hiveplane.drift.reinstatement import ReinstatementService
from hiveplane.drift.scheduler import DriftScheduler
from hiveplane.events.service import FleetEventService
from hiveplane.execution.service import RunService
from hiveplane.execution.tools import ToolGateway
from hiveplane.federation.service import FederationService
from hiveplane.ha.leader import LeaderElector
from hiveplane.health.plane_metrics import PlaneMetrics
from hiveplane.health.service import HealthService
from hiveplane.incident.service import IncidentService
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.eval import EvalService
from hiveplane.learning.feedback import FeedbackService
from hiveplane.mcp.registry import McpRegistry
from hiveplane.persistence.audit import AuditLog
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
from hiveplane.replay.service import ReplayService
from hiveplane.reporting.audit_export import AuditExportService
from hiveplane.reporting.digest import DigestService
from hiveplane.reporting.evidence import EvidencePackService
from hiveplane.reporting.pii import PIIScrubber
from hiveplane.reporting.purge import TenantPurgeService
from hiveplane.reporting.retention import RetentionEnforcer
from hiveplane.reporting.scheduler import DigestScheduler
from hiveplane.reporting.store import ReportingStore
from hiveplane.router.engine import RouterEngine
from hiveplane.router.store import RouterStore
from hiveplane.scheduler.scheduler import Scheduler
from hiveplane.secrets.service import SecretService
from hiveplane.tenancy import Role, TenantContext, TenantSuspendedError
from hiveplane.tenancy.admin import TenantAdminService
from hiveplane.tenancy.context import DEFAULT_CONTEXT, SYSTEM_CONTEXT, SYSTEM_TENANT_ID
from hiveplane.transparency.verify import PublicVerifier
from hiveplane.triggers.engine import TriggerEngine
from hiveplane.triggers.freeze import FreezeService
from hiveplane.triggers.ingest import WebhookVerifier
from hiveplane.triggers.store import TriggerStore
from hiveplane.worker.registry import WorkerRegistry

if TYPE_CHECKING:
    from hiveplane.config import Settings

TENANT_HEADER = "X-Hiveplane-Tenant"
TEAM_HEADER = "X-Hiveplane-Team"


def resolve_tenant(
    request: Request,
    principal: OperatorIdentity | None,
    settings: Settings,
) -> TenantContext:
    """Reconcile the authenticated principal with the tenant/team headers.

    The single tenancy boundary. With auth disabled the header is trusted
    exactly as before; with auth enabled the principal's tenant wins and a
    mismatched header is rejected.
    """
    tenant_id = request.headers.get(TENANT_HEADER)
    team_id = request.headers.get(TEAM_HEADER)
    if not settings.auth.enabled:
        if tenant_id is None:
            return DEFAULT_CONTEXT
        return TenantContext(tenant_id=tenant_id, team_id=team_id, role=Role.ADMIN)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    if principal.tenant_id == SYSTEM_TENANT_ID:
        if tenant_id is None:
            return TenantContext(
                tenant_id=SYSTEM_TENANT_ID,
                team_id=team_id,
                operator_id=principal.operator_id,
                role=Role.ADMIN,
                is_system=True,
            )
        return TenantContext(
            tenant_id=tenant_id,
            team_id=team_id,
            operator_id=principal.operator_id,
            role=Role.ADMIN,
        )
    if tenant_id is not None and tenant_id != principal.tenant_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "tenant header does not match the authenticated principal",
        )
    return TenantContext(
        tenant_id=principal.tenant_id,
        team_id=team_id,
        operator_id=principal.operator_id,
        role=principal.role,
    )


def get_tenant_context(request: Request) -> TenantContext:
    """Resolve the acting tenant, reconciling identity when auth is enabled.

    ``X-Hiveplane-Tenant`` selects the tenant and ``X-Hiveplane-Team`` the team.
    With authentication disabled the header is trusted and absent headers fall
    back to the seeded default tenant. With authentication enabled the tenant
    comes from the principal (or, for the system principal, the header).
    """
    from hiveplane.config import get_settings

    settings = get_settings()
    principal = get_principal(request) if settings.auth.enabled else None
    return resolve_tenant(request, principal, settings)


def get_ingest_tenant_context(request: Request) -> TenantContext:
    """Resolve the tenant for HMAC-authenticated ingest endpoints.

    Webhook/A2A senders cannot carry the operator bearer key, so these endpoints
    bypass ``get_principal`` and rely on their own signature/plane authentication.
    A tenant header selects the tenant when present; otherwise the trigger's own
    declared tenant is used so fixed-header senders (GitHub, Alertmanager) work.
    """
    from hiveplane.config import get_settings

    settings = get_settings()
    if not settings.auth.enabled:
        return get_tenant_context(request)
    tenant_id = request.headers.get(TENANT_HEADER)
    team_id = request.headers.get(TEAM_HEADER)
    if tenant_id is None:
        trigger_id = request.path_params.get("trigger_id")
        store = getattr(request.app.state, "trigger_store", None)
        if trigger_id is not None and store is not None:
            spec = store.get_trigger(trigger_id, ctx=SYSTEM_CONTEXT)
            if spec is not None:
                tenant_id = spec.tenant_id
    if tenant_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "tenant required for signature-authenticated ingest"
        )
    return TenantContext(tenant_id=tenant_id, team_id=team_id, role=Role.VIEWER)


def get_registry_service(request: Request) -> RegistryService:
    """Return the registry service bound to the application state."""
    service: RegistryService = request.app.state.registry_service
    return service


def get_run_service(request: Request) -> RunService:
    """Return the run service bound to the application state."""
    service: RunService = request.app.state.run_service
    return service


def get_tenant_admin_service(request: Request) -> TenantAdminService:
    """Return the tenant admin service bound to the application state."""
    service: TenantAdminService = request.app.state.tenant_admin_service
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


def get_cost_estimator(request: Request) -> CostEstimator:
    """Return the cost estimator bound to the application state."""
    estimator: CostEstimator = request.app.state.cost_estimator
    return estimator


def get_result_cache(request: Request) -> ResultCache:
    """Return the attested result cache bound to the application state."""
    cache: ResultCache = request.app.state.result_cache
    return cache


def get_delivery_service(request: Request) -> DeliveryService:
    """Return the delivery service bound to the application state."""
    service: DeliveryService = request.app.state.delivery_service
    return service


def get_interactive_approvals(request: Request) -> InteractiveApprovalService:
    """Return the interactive-approval service bound to the application state."""
    service: InteractiveApprovalService = request.app.state.interactive_approvals
    return service


def get_cost_service(request: Request) -> CostService:
    """Return the cost service bound to the application state."""
    service: CostService = request.app.state.cost_service
    return service


def get_leader_elector(request: Request) -> LeaderElector:
    """Return the leader elector bound to the application state."""
    elector: LeaderElector = request.app.state.leader_elector
    return elector


def get_chaos_engine(request: Request) -> ChaosEngine:
    """Return the chaos engine bound to the application state."""
    engine: ChaosEngine = request.app.state.chaos_engine
    return engine


def get_scheduler(request: Request) -> Scheduler:
    """Return the fleet scheduler bound to the application state."""
    scheduler: Scheduler = request.app.state.scheduler
    return scheduler


def get_worker_registry(request: Request) -> WorkerRegistry:
    """Return the worker registry bound to the application state."""
    registry: WorkerRegistry = request.app.state.worker_registry
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
    except TenantSuspendedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


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


def get_incident_service(request: Request) -> IncidentService:
    """Return the incident-mode service bound to the application state."""
    service: IncidentService = request.app.state.incident_service
    return service


def get_ask_service(request: Request) -> AskService:
    """Return the `ask` copilot, built lazily over live control-plane services."""
    app = request.app
    service: AskService | None = getattr(app.state, "ask_service", None)
    if service is None:
        service = AskService(
            ServiceReaders(
                run_service=app.state.run_service,
                cost_service=app.state.cost_service,
                approval_service=app.state.approval_service,
                health_service=app.state.health_service,
            ),
            audit=getattr(app.state, "audit_log", None),
        )
        app.state.ask_service = service
    return service


def get_artifact_service(request: Request) -> ArtifactService:
    """Return the artifact service bound to the application state."""
    service: ArtifactService = request.app.state.artifact_service
    return service


def get_retention_service(request: Request) -> RetentionService:
    """Return the artifact retention service bound to the application state."""
    service: RetentionService = request.app.state.retention_service
    return service


def get_retention_enforcer(request: Request) -> RetentionEnforcer:
    """Return the per-tenant retention enforcer bound to application state."""
    enforcer: RetentionEnforcer = request.app.state.retention_enforcer
    return enforcer


def get_tenant_purge_service(request: Request) -> TenantPurgeService:
    """Return the tenant purge service bound to application state."""
    service: TenantPurgeService = request.app.state.tenant_purge_service
    return service


def get_pii_scrubber(request: Request) -> PIIScrubber:
    """Return the PII scrubber bound to application state."""
    scrubber: PIIScrubber = request.app.state.pii_scrubber
    return scrubber


def get_corpus_service(request: Request) -> CorpusService:
    """Return the corpus publishing service bound to the application state."""
    service: CorpusService = request.app.state.corpus_service
    return service


def get_fleet_event_service(request: Request) -> FleetEventService:
    """Return the fleet-events subscription service bound to application state."""
    service: FleetEventService = request.app.state.fleet_event_service
    return service


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


def get_reporting_store(request: Request) -> ReportingStore:
    """Return the reporting store bound to the application state."""
    store: ReportingStore = request.app.state.reporting_store
    return store


def get_backup_service(request: Request) -> BackupService:
    """Return the control-plane backup service bound to the application state."""
    service: BackupService = request.app.state.backup_service
    return service


def get_replay_service(request: Request) -> ReplayService:
    """Return the replay service bound to the application state."""
    service: ReplayService = request.app.state.replay_service
    return service


def get_demo_seeder(request: Request) -> DemoSeeder:
    """Return the demo-profile seeder bound to the application state."""
    seeder: DemoSeeder = request.app.state.demo_seeder
    return seeder


def get_federation_service(request: Request) -> FederationService:
    """Return the federation service, or 503 when federation is disabled."""
    from hiveplane.config import get_settings

    if not get_settings().federation.enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "federation is disabled; set HIVEPLANE_FEDERATION__ENABLED=true",
        )
    service: FederationService | None = getattr(
        request.app.state, "federation_service", None
    )
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "federation service is not configured"
        )
    return service


def get_audit_log(request: Request) -> AuditLog:
    """Return the tamper-evident audit log bound to the application state."""
    log: AuditLog = request.app.state.audit_log
    return log


def get_audit_export_service(request: Request) -> AuditExportService:
    """Return the audit export service bound to the application state."""
    service: AuditExportService = request.app.state.audit_export_service
    return service


def get_evidence_service(request: Request) -> EvidencePackService:
    """Return the evidence-pack service, or 503 when unavailable."""
    service: EvidencePackService | None = getattr(
        request.app.state, "evidence_service", None
    )
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "compliance evidence packs are unavailable",
        )
    return service


def get_digest_service(request: Request) -> DigestService:
    """Return the fleet digest service, or 503 when drift is disabled."""
    service: DigestService | None = getattr(request.app.state, "digest_service", None)
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "drift detection is not enabled; fleet digest is unavailable",
        )
    return service


def get_digest_scheduler(request: Request) -> DigestScheduler:
    """Return the digest scheduler, or 503 when drift is disabled."""
    scheduler: DigestScheduler | None = getattr(
        request.app.state, "digest_scheduler", None
    )
    if scheduler is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "drift detection is not enabled; digest scheduling is unavailable",
        )
    return scheduler


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
