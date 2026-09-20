"""HTTP service (F-62, §5.9): REST surface for the PlannerCritic engine.

Provides a :class:`PlannerCriticHTTPServer` that routes method+path to
store/engine operations and returns dict responses — testable without any
web framework. A :func:`create_fastapi_app` factory is included for when
FastAPI is available (import-safe try/except pattern).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, cast

from ..engine import Engine
from ..escalation import EscalationManager, build_escalation_manager
from ..explain import explain as build_explain
from ..gates import run_deterministic_gates
from ..llm.registry import ProviderRegistry
from ..loop import LoopConfig
from ..roles import PlannerRole
from ..schema.goal import Goal
from ..schema.plan import PlanVersion
from ..store.base import PlanStore
from ..store.sqlite import SQLiteStore
from ..types import Finding
from ..viz.graph import to_mermaid


class PlannerCriticHTTPServer:
    """REST surface for PlannerCritic operations.

    Args:
        store_path: Path to the SQLite plan-store database.
        config_path: Optional path to engine/provider configuration.
    """

    def __init__(self, store_path: str, config_path: str | None = None) -> None:
        self._store_path = store_path
        self._config_path = config_path
        self._store: PlanStore | None = None
        self._engine: Engine | None = None
        # Caching for provider-backed builds: the planner, registry, and loop
        # config are goal-agnostic, so they are reused across requests. Only
        # the goal-bound critic is rebuilt per request.
        self._planner: PlannerRole | None = None
        self._registry: ProviderRegistry | None = None
        self._loop_config: LoopConfig | None = None

    # ---- Store lifecycle ---------------------------------------------------

    @property
    def store(self) -> PlanStore:
        if self._store is None:
            self._store = SQLiteStore(self._store_path)
        return self._store

    def set_engine(self, engine: Engine) -> None:
        self._engine = engine

    def _build_engine(self, goal: Goal) -> Engine:
        """Build per-goal roles from the configured provider registry.

        Mirrors mcp.py's _build_engine so HTTP serves the same provider-bound
        planner/critic loop. Falls back to self._engine if explicitly set via
        :meth:`set_engine` (hermetic tests). Loop config is read from PC_*
        env vars so the container can tune revision cap and critique mode.

        The goal-agnostic planner, registry, and loop config are cached so
        repeated requests reuse the same LLM transport (and its httpx
        connection pool). Only the goal-bound critic is rebuilt per request.
        """
        if self._engine is not None:
            return self._engine
        if self._config_path is None:
            raise ValueError(
                "no engine configured and no provider config given "
                "(call set_engine() or pass config_path)"
            )
        if self._registry is None:
            self._registry = ProviderRegistry.load(self._config_path)
        if self._loop_config is None:
            self._loop_config = LoopConfig.from_env()
        from ..cli.plan import _CLIPlanner
        from ..critique.critic import LLMCritic

        if self._planner is None:
            self._planner = _CLIPlanner(self._registry.get_provider("planner"))
        planner = self._planner
        critic = LLMCritic(goal, self._registry.get_provider("critic"))
        return Engine(planner, critic, config=self._loop_config)

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    # ---- Router ------------------------------------------------------------

    def handle_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Route a method+path to the matching handler.

        Args:
            method: HTTP method (GET, POST).
            path: The request path (e.g. ``/plans``, ``/plans/{id}``).
            body: Optional JSON body for POST requests.

        Returns:
            A dict with ``status`` (HTTP status code) and ``data`` (response
            payload) or ``error`` on failure.
        """
        try:
            return self._route(method, path, body or {})
        except ValueError as exc:
            return {"status": 400, "error": str(exc)}
        except FileNotFoundError as exc:
            return {"status": 404, "error": str(exc)}
        except Exception as exc:
            return {"status": 500, "error": f"{type(exc).__name__}: {exc}"}

    # ---- Internal routing --------------------------------------------------

    _PATH_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str, str]]] = []

    @classmethod
    def _compile_patterns(cls) -> None:
        if cls._PATH_PATTERNS:
            return
        cls._PATH_PATTERNS = [
            (re.compile(r"^/plans/([^/]+)/diff$"), "GET", "plan_diff"),
            (re.compile(r"^/plans/([^/]+)/graph$"), "GET", "plan_graph"),
            (re.compile(r"^/plans/([^/]+)/explain$"), "GET", "plan_explain"),
            (re.compile(r"^/plans/([^/]+)$"), "GET", "plan_detail"),
            (re.compile(r"^/plans$"), "GET", "list_plans"),
            (re.compile(r"^/plan$"), "POST", "plan_goal"),
            (re.compile(r"^/critique$"), "POST", "critique_plan"),
            (re.compile(r"^/escalations/([^/]+)/approve$"), "POST", "escalate_approve"),
            (re.compile(r"^/escalations/([^/]+)/deny$"), "POST", "escalate_deny"),
            (re.compile(r"^/escalations$"), "GET", "list_escalations"),
        ]

    def _route(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self._compile_patterns()
        for pattern, expected_method, handler_name in self._PATH_PATTERNS:
            match = pattern.match(path)
            if match and method == expected_method:
                args: tuple[Any, ...] = (*match.groups(),)
                handler = getattr(self, f"_handle_{handler_name}")
                return cast("dict[str, Any]", handler(*args, body=body))
        return {"status": 404, "error": f"unknown route: {method} {path}"}

    # ---- Handler: POST /plan ------------------------------------------------

    def _handle_plan_goal(self, body: dict[str, Any]) -> dict[str, Any]:
        goal = Goal.model_validate(body)
        try:
            engine = self._build_engine(goal)
        except Exception as exc:
            return {"status": 501, "error": str(exc)}
        result = engine.plan(goal)
        store = self.store
        if result.plan is not None:
            store.put_plan_version(result.plan)
            store.put_findings(result.plan.id, result.plan.version, result.findings)
        if result.escalation is not None:
            store.put_escalation(result.escalation)
        return {
            "status": 200,
            "data": {
                "status": result.status,
                "plan": result.plan.model_dump(mode="json") if result.plan else None,
                "findings": [f.model_dump(mode="json") for f in result.findings],
                "reason_code": result.reason_code,
                "escalation": (
                    result.escalation.model_dump(mode="json") if result.escalation else None
                ),
            },
        }

    # ---- Handler: POST /critique --------------------------------------------

    def _handle_critique_plan(self, body: dict[str, Any]) -> dict[str, Any]:
        plan = PlanVersion.model_validate(body.get("plan", body))
        goal_data = body.get("goal")
        gate_findings = run_deterministic_gates(plan)
        critic_findings: list[Finding] = list(gate_findings)
        if goal_data is not None:
            try:
                goal = Goal.model_validate(goal_data)
                engine = self._build_engine(goal)
                critic_findings = engine.critic.audit(plan, list(gate_findings))
            except Exception as exc:
                return {"status": 501, "error": str(exc)}
        store = self.store
        store.put_plan_version(plan)
        store.put_findings(plan.id, plan.version, critic_findings)
        return {
            "status": 200,
            "data": {
                "plan_id": plan.id,
                "version": plan.version,
                "findings": [f.model_dump(mode="json") for f in critic_findings],
            },
        }

    # ---- Handler: GET /plans ------------------------------------------------

    def _handle_list_plans(self, body: dict[str, Any]) -> dict[str, Any]:
        plans = self.store.list_plans()
        seen: set[str] = set()
        latest: list[PlanVersion] = []
        for p in plans:
            if p.id not in seen:
                seen.add(p.id)
                latest.append(p)
        return {
            "status": 200,
            "data": {
                "plans": [p.model_dump(mode="json") for p in latest],
                "count": len(latest),
            },
        }

    # ---- Handler: GET /plans/{id} -------------------------------------------

    def _handle_plan_detail(self, plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            return {"status": 404, "error": f"plan {plan_id!r} not found"}
        return {
            "status": 200,
            "data": {
                "plan": plan.model_dump(mode="json"),
            },
        }

    # ---- Handler: GET /plans/{id}/diff --------------------------------------

    def _handle_plan_diff(self, plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
        v2 = body.get("v2") if body else None
        if v2 is None:
            return {"status": 400, "error": "query parameter v2 is required"}
        try:
            version_b = int(v2)
        except (TypeError, ValueError):
            return {"status": 400, "error": "v2 must be an integer"}
        plan_latest = self.store.get_plan(plan_id)
        if plan_latest is None:
            return {"status": 404, "error": f"plan {plan_id!r} not found"}
        version_a = 1
        diff = self.store.diff(plan_id, version_a, version_b)
        if diff is None:
            return {
                "status": 404,
                "error": "could not compute diff — revisions missing",
            }
        return {
            "status": 200,
            "data": diff.model_dump(mode="json"),
        }

    # ---- Handler: GET /plans/{id}/graph -------------------------------------

    def _handle_plan_graph(self, plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            return {"status": 404, "error": f"plan {plan_id!r} not found"}
        mermaid = to_mermaid(plan)
        return {
            "status": 200,
            "data": {"plan_id": plan_id, "version": plan.version, "mermaid": mermaid},
        }

    # ---- Handler: GET /plans/{id}/explain -----------------------------------

    def _handle_plan_explain(self, plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
        result = build_explain(self.store, plan_id)
        return {
            "status": 200,
            "data": result.model_dump(mode="json"),
        }

    # ---- Handler: GET /escalations ------------------------------------------

    def _handle_list_escalations(self, body: dict[str, Any]) -> dict[str, Any]:
        manager = EscalationManager(self.store)
        escalations = manager.list_escalations()
        return {
            "status": 200,
            "data": {
                "escalations": [e.model_dump(mode="json") for e in escalations],
                "count": len(escalations),
            },
        }

    # ---- Handler: POST /escalations/{id}/approve ----------------------------

    def _handle_escalate_approve(self, escalation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        manager = build_escalation_manager(self.store, escalation_id=escalation_id)
        note = (body or {}).get("note", "")
        principal = (body or {}).get("principal")
        resolved = manager.resolve(escalation_id, "approved", note=note, principal=principal)
        return {
            "status": 200,
            "data": resolved.model_dump(mode="json"),
        }

    # ---- Handler: POST /escalations/{id}/deny -------------------------------

    def _handle_escalate_deny(self, escalation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        manager = build_escalation_manager(self.store, escalation_id=escalation_id)
        note = (body or {}).get("note", "")
        principal = (body or {}).get("principal")
        resolved = manager.resolve(escalation_id, "denied", note=note, principal=principal)
        return {
            "status": 200,
            "data": resolved.model_dump(mode="json"),
        }


# ---- FastAPI adapter (import-safe) -------------------------------------------


def create_fastapi_app(store_path: str, config_path: str | None = None) -> Any | None:
    """Build a FastAPI application wrapping the PlannerCritic HTTP surface.

    Args:
        store_path: Path to the SQLite plan-store database.
        config_path: Optional path to the provider TOML config. When provided,
            the server builds per-goal roles from the registry (used by the
            container entrypoint). When omitted, ``set_engine()`` must be called
            before ``/plan`` or ``/critique`` (hermetic tests).

    Returns:
        A configured ``FastAPI`` instance, or ``None`` if FastAPI is not
        available.
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        return None

    server = PlannerCriticHTTPServer(store_path, config_path=config_path)
    app = FastAPI(title="PlannerCritic Engine", version="0.1.0")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        server.close()

    @app.post("/plan")
    async def post_plan(body: dict[str, Any]) -> dict[str, Any]:
        return server.handle_request("POST", "/plan", body)

    @app.post("/critique")
    async def post_critique(body: dict[str, Any]) -> dict[str, Any]:
        return server.handle_request("POST", "/critique", body)

    @app.get("/plans")
    async def get_plans() -> dict[str, Any]:
        return server.handle_request("GET", "/plans")

    @app.get("/plans/{plan_id}")
    async def get_plan(plan_id: str) -> dict[str, Any]:
        return server.handle_request("GET", f"/plans/{plan_id}")

    @app.get("/plans/{plan_id}/diff")
    async def get_plan_diff(plan_id: str, v2: int) -> dict[str, Any]:
        return server.handle_request("GET", f"/plans/{plan_id}/diff", {"v2": str(v2)})

    @app.get("/plans/{plan_id}/graph")
    async def get_plan_graph(plan_id: str) -> dict[str, Any]:
        return server.handle_request("GET", f"/plans/{plan_id}/graph")

    @app.get("/plans/{plan_id}/explain")
    async def get_plan_explain(plan_id: str) -> dict[str, Any]:
        return server.handle_request("GET", f"/plans/{plan_id}/explain")

    @app.get("/escalations")
    async def get_escalations() -> dict[str, Any]:
        return server.handle_request("GET", "/escalations")

    @app.post("/escalations/{escalation_id}/approve")
    async def post_escalate_approve(
        escalation_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return server.handle_request("POST", f"/escalations/{escalation_id}/approve", body or {})

    @app.post("/escalations/{escalation_id}/deny")
    async def post_escalate_deny(
        escalation_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return server.handle_request("POST", f"/escalations/{escalation_id}/deny", body or {})

    @app.get("/healthz")
    async def get_healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


__all__ = ["PlannerCriticHTTPServer", "create_fastapi_app"]
