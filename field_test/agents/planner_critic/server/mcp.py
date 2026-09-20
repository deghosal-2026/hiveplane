"""MCP server for the PlannerCritic Engine (M5 T7).

Provides 6 tools via a transport-agnostic ``PlannerCriticMCPServer``:

* ``plan`` — run ``Engine.plan()`` with a goal
* ``critique`` — run deterministic gates + optional LLM critic on a plan
* ``explain`` — stub (not yet implemented)
* ``escalate_list`` — list open escalations
* ``escalate_approve`` — approve an escalation
* ``escalate_deny`` — deny an escalation

The server is deliberately transport-agnostic: ``run_stdio()`` provides a
JSON-lines protocol over stdio, and ``handle_tool()`` / ``list_tools()`` let
any transport (MCP FastMCP, HTTP, gRPC) wrap the same logic.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ..critique.critic import LLMCritic  # noqa: F401  (re-exported for callers)
from ..engine import Engine
from ..gates import run_deterministic_gates
from ..llm.registry import ProviderRegistry
from ..loop import LoopConfig
from ..roles import CriticRole, PlannerRole
from ..schema.goal import Goal
from ..schema.plan import PlanVersion
from ..types import PlanningError
from . import mcp_tools_escalate as esc


def _goal_from_json(goal_json: str) -> Goal:
    """Parse a JSON string into a :class:`Goal`."""
    return Goal.model_validate_json(goal_json)


def _plan_from_json(plan_json: str) -> PlanVersion:
    """Parse a JSON string into a :class:`PlanVersion`."""
    return PlanVersion.model_validate_json(plan_json)


# -- tool schemas ----------------------------------------------------------

_TOOL_DEFINITIONS = [
    {
        "name": "plan",
        "description": "Plan a goal end-to-end (planner + critic loop).",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_json": {
                    "type": "string",
                    "description": "Serialized Goal JSON",
                }
            },
            "required": ["goal_json"],
        },
    },
    {
        "name": "critique",
        "description": "Run deterministic gates (and optional LLM critic) on a plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_json": {
                    "type": "string",
                    "description": "Serialized PlanVersion JSON",
                }
            },
            "required": ["plan_json"],
        },
    },
    {
        "name": "explain",
        "description": "Explain why the loop decided what it did (stub).",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "string",
                    "description": "Plan id to explain",
                }
            },
            "required": ["plan_id"],
        },
    },
    {
        "name": "escalate_list",
        "description": "List escalations, optionally filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter: open, approved, denied, or null for all",
                }
            },
        },
    },
    {
        "name": "escalate_approve",
        "description": "Approve an escalation (optionally patching the plan first).",
        "input_schema": {
            "type": "object",
            "properties": {
                "escalation_id": {"type": "string"},
                "note": {"type": "string"},
                "patch_json": {
                    "type": "string",
                    "description": "Optional PlanVersion JSON to patch",
                },
                "principal": {
                    "type": "string",
                    "description": "Approving principal (required when approving_authority is set)",
                },
            },
            "required": ["escalation_id"],
        },
    },
    {
        "name": "escalate_deny",
        "description": "Deny an escalation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "escalation_id": {"type": "string"},
                "note": {"type": "string"},
                "principal": {
                    "type": "string",
                    "description": "Denying principal (required when approving_authority is set)",
                },
            },
            "required": ["escalation_id"],
        },
    },
]


# -- server class -----------------------------------------------------------


class PlannerCriticMCPServer:
    """Transport-agnostic MCP server for PlannerCritic tools.

    Args:
        store_path: Path to the SQLite plan store.
        llm_config_path: Optional path to the provider TOML config.
        planner: Optional pre-configured planner role.
        critic: Optional pre-configured critic role.
        loop_config: Optional loop configuration.
    """

    def __init__(
        self,
        store_path: str,
        llm_config_path: str | None = None,
        planner: PlannerRole | None = None,
        critic: CriticRole | None = None,
        loop_config: LoopConfig | None = None,
    ) -> None:
        self.store_path = store_path
        self.llm_config_path = llm_config_path
        self._planner = planner
        self._critic = critic
        self.loop_config = loop_config

    # -- public transport-agnostic API ------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Return the 6 tool definitions."""
        return list(_TOOL_DEFINITIONS)

    def handle_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call and return a result dict.

        Args:
            name: Tool name (``plan``, ``critique``, ``explain``,
                ``escalate_list``, ``escalate_approve``, ``escalate_deny``).
            args: Tool-specific arguments.

        Returns:
            A serializable result dictionary. Top-level keys always include
            ``"status"`` (``"ok"`` or ``"error"``).
        """
        handlers = {
            "plan": self._handle_plan,
            "critique": self._handle_critique,
            "explain": self._handle_explain,
            "escalate_list": self._handle_escalate_list,
            "escalate_approve": self._handle_escalate_approve,
            "escalate_deny": self._handle_escalate_deny,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"status": "error", "error": f"unknown tool: {name}"}
        return handler(**args)  # type: ignore[call-arg]

    def run_stdio(self) -> None:
        """Run the server over stdin/stdout JSON-lines protocol.

        Each line of stdin is a JSON object with ``tool`` (str) and
        ``args`` (dict). Each line of stdout is a JSON response.
        """
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                name = request["tool"]
                args = request.get("args", {})
                result = self.handle_tool(name, args)
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()

    # -- tool handlers ----------------------------------------------------

    def _handle_plan(self, goal_json: str) -> dict[str, Any]:
        """Create a plan for a goal end-to-end.

        Returns:
            A dict with ``status``, and on success ``result`` (LoopResult
            as JSON), or ``error`` on failure.
        """
        try:
            goal = _goal_from_json(goal_json)
        except Exception as exc:
            return {"status": "error", "error": f"invalid goal_json: {exc}"}

        try:
            engine = self._build_engine(goal)
        except PlanningError as exc:
            return {"status": "error", "error": str(exc)}

        try:
            result = engine.plan(goal)
            return {
                "status": "ok",
                "result": _loop_result_to_dict(result),
            }
        except Exception as exc:
            return {"status": "error", "error": f"planning failed: {exc}"}

    def _handle_critique(self, plan_json: str) -> dict[str, Any]:
        """Run deterministic gates (and optional LLM critic) on a plan.

        Returns:
            A dict with ``status``, and on success ``findings`` (list) and
            ``meets_threshold`` (bool).
        """
        try:
            plan = _plan_from_json(plan_json)
        except Exception as exc:
            return {"status": "error", "error": f"invalid plan_json: {exc}"}

        gate_findings = run_deterministic_gates(plan)

        if self._critic is not None:
            try:
                critic_findings = self._critic.audit(plan, list(gate_findings))
            except Exception as exc:
                return {
                    "status": "error",
                    "error": f"critic audit failed: {exc}",
                    "gate_findings": [f.model_dump(mode="json") for f in gate_findings],
                }
        else:
            critic_findings = list(gate_findings)

        has_blocker = any(f.severity.value == "blocker" for f in critic_findings)

        return {
            "status": "ok",
            "findings": [f.model_dump(mode="json") for f in critic_findings],
            "has_blocker": has_blocker,
        }

    def _handle_explain(self, plan_id: str) -> dict[str, Any]:
        """Explain why the loop decided what it did (stub)."""
        return {
            "status": "ok",
            "result": "explain not yet implemented",
        }

    def _handle_escalate_list(self, status: str | None = None) -> dict[str, Any]:
        """List escalations from the plan store."""
        try:
            items = esc.escalate_list(self.store_path, status=status)
            return {"status": "ok", "escalations": items}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def _handle_escalate_approve(
        self,
        escalation_id: str,
        note: str = "",
        patch_json: str | None = None,
        principal: str | None = None,
    ) -> dict[str, Any]:
        """Approve an escalation."""
        try:
            result = esc.escalate_approve(
                self.store_path,
                escalation_id,
                note=note,
                patch_json=patch_json,
                principal=principal,
            )
            return {"status": "ok", "escalation": result}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def _handle_escalate_deny(
        self,
        escalation_id: str,
        note: str = "",
        principal: str | None = None,
    ) -> dict[str, Any]:
        """Deny an escalation."""
        try:
            result = esc.escalate_deny(
                self.store_path, escalation_id, note=note, principal=principal
            )
            return {"status": "ok", "escalation": result}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # -- internals --------------------------------------------------------

    def _build_engine(self, goal: Goal) -> Engine:
        """Build an Engine from configured roles or provider config.

        When explicit roles are provided (planner + critic), they are used
        directly and cached across requests. When roles are built from config,
        the planner is cached (goal-agnostic) but the critic is rebuilt per
        goal because its audit logic is bound to the specific goal.

        Raises:
            PlanningError: When neither explicit roles nor a valid provider
                config are available.
        """
        if self._planner is not None and self._critic is not None:
            return Engine(self._planner, self._critic, config=self.loop_config)

        if self.llm_config_path is None:
            raise PlanningError("no providers configured (llm_config_path not provided)")

        registry = ProviderRegistry.load(self.llm_config_path)
        from ..cli.plan import _build_roles

        if self._planner is None:
            planner, _ = _build_roles(registry, goal)
            self._planner = planner

        _, critic = _build_roles(registry, goal)

        return Engine(self._planner, critic, config=self.loop_config)


# -- helpers ---------------------------------------------------------------


def _loop_result_to_dict(result: Any) -> dict[str, Any]:
    """Serialize a LoopResult to a plain dict."""
    base = {
        "status": result.status,
        "reason_code": result.reason_code,
        "mode": result.mode.value if hasattr(result.mode, "value") else result.mode,
    }
    if result.approved_plan is not None:
        base["approved_plan"] = result.approved_plan.model_dump(mode="json")
    if result.plan is not None:
        base["plan_id"] = result.plan.id
        base["plan_version"] = result.plan.version
    if result.escalation is not None:
        base["escalation"] = result.escalation.model_dump(mode="json")
    if result.spend is not None:
        base["spend"] = {
            "revisions_used": result.spend.revisions_used,
            "calls_used": result.spend.calls_used,
        }
    return base


def create_server(
    store_path: str,
    llm_config_path: str | None = None,
    planner: PlannerRole | None = None,
    critic: CriticRole | None = None,
    loop_config: LoopConfig | None = None,
) -> PlannerCriticMCPServer:
    """Factory: build a fully-configured :class:`PlannerCriticMCPServer`.

    Args:
        store_path: Path to the SQLite plan store.
        llm_config_path: Optional path to the provider TOML config.
        planner: Optional pre-configured planner role.
        critic: Optional pre-configured critic role.
        loop_config: Optional loop configuration.

    Returns:
        A ready-to-use server instance.
    """
    return PlannerCriticMCPServer(
        store_path=store_path,
        llm_config_path=llm_config_path,
        planner=planner,
        critic=critic,
        loop_config=loop_config,
    )


__all__ = [
    "PlannerCriticMCPServer",
    "create_server",
]
