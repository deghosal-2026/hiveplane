"""Unit tests for agent-as-tool invocation (M30-04..M30-06, #202-#204)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from hiveplane.agent_tools.engine import AgentToolInvoker
from hiveplane.agent_tools.models import (
    AgentToolNotFoundError,
    AgentToolRefusedError,
    InvocationDecision,
)
from hiveplane.agent_tools.registry import (
    AgentToolRegistry,
    tool_id_for,
    workload_for,
)
from hiveplane.agent_tools.store import InMemoryAgentToolStore
from hiveplane.core.run import AdmissionContext
from hiveplane.execution.errors import RunAdmissionRefusedError
from hiveplane.execution.models import AdmissionResult


class FakeRegistry:
    def __init__(
        self,
        workloads: dict[str, str],
        admitted: set[str] | None = None,
    ) -> None:
        self._workloads = workloads
        self._admitted = admitted if admitted is not None else set(workloads)

    def list_workloads(self) -> list[Any]:
        return [
            SimpleNamespace(
                name=name,
                manifest=SimpleNamespace(
                    metadata=SimpleNamespace(description=description)
                ),
            )
            for name, description in self._workloads.items()
        ]

    def check_admission(self, name: str, context: AdmissionContext) -> Any:
        admitted = name in self._admitted
        return SimpleNamespace(
            admitted=admitted,
            reason=None if admitted else "not certified",
        )


class FakeRunService:
    def __init__(self, *, refuse: bool = False) -> None:
        self.submissions: list[dict[str, Any]] = []
        self._refuse = refuse

    def submit(self, **kwargs: Any) -> Any:
        self.submissions.append(kwargs)
        if self._refuse:
            raise RunAdmissionRefusedError(
                cast(
                    AdmissionResult,
                    SimpleNamespace(
                        refused_reason="policy denied",
                        workload="triage",
                        context=AdmissionContext.STAGING,
                    ),
                )
            )
        return SimpleNamespace(id="run-nested", cost_usd=0.25)


def _invoker(
    *,
    admitted: set[str] | None = None,
    max_depth: int = 5,
    store: InMemoryAgentToolStore | None = None,
    refuse: bool = False,
) -> tuple[AgentToolInvoker, FakeRunService, FakeRegistry]:
    registry = FakeRegistry({"triage": "triage agent", "remediate": "fix agent"}, admitted)
    runs = FakeRunService(refuse=refuse)
    invoker = AgentToolInvoker(
        AgentToolRegistry(registry), runs, store=store, max_depth=max_depth
    )
    return invoker, runs, registry


def test_tool_id_round_trip() -> None:
    assert tool_id_for("triage") == "agent.triage"
    assert workload_for("agent.triage") == "triage"
    with pytest.raises(AgentToolNotFoundError):
        workload_for("triage")
    with pytest.raises(AgentToolNotFoundError):
        workload_for("agent.")


def test_list_tools_excludes_uncertified() -> None:
    invoker, _, _ = _invoker(admitted={"triage"})
    registry = AgentToolRegistry(
        FakeRegistry({"triage": "t", "remediate": "r"}, {"triage"})
    )
    tools = registry.list_tools(AdmissionContext.PRODUCTION)
    assert [tool.tool_id for tool in tools] == ["agent.triage"]
    assert tools[0].description == "t"
    assert invoker is not None


def test_invoke_allowed_submits_nested_run_with_origin() -> None:
    invoker, runs, _ = _invoker()
    invocation = invoker.invoke(
        "agent.triage", caller_run_id="run-caller", task={"x": 1}
    )
    assert invocation.decision is InvocationDecision.ALLOWED
    assert invocation.allowed is True
    assert invocation.nested_run_id == "run-nested"
    assert invocation.depth == 1
    assert invocation.chain == ["triage"]
    origin = runs.submissions[0]["agent_tool_origin"]
    assert origin.caller_run_id == "run-caller"
    assert origin.depth == 1
    assert origin.chain == ["triage"]
    assert runs.submissions[0]["context"] is AdmissionContext.STAGING


def test_invoke_uncertified_workload_is_refused() -> None:
    invoker, runs, _ = _invoker(admitted={"triage"})
    invocation = invoker.invoke("agent.remediate", caller_run_id="run-caller")
    assert invocation.decision is InvocationDecision.REFUSED
    assert "not certified" in (invocation.reason or "")
    assert runs.submissions == []


def test_invoke_unknown_tool_is_refused() -> None:
    invoker, _, _ = _invoker()
    invocation = invoker.invoke("agent.ghost", caller_run_id="run-caller")
    assert invocation.decision is InvocationDecision.REFUSED
    assert "unknown" in (invocation.reason or "")


def test_depth_limit_is_rejected() -> None:
    invoker, runs, _ = _invoker(max_depth=2)
    allowed = invoker.invoke(
        "agent.triage", caller_run_id="run-caller", depth=1, chain=["other"]
    )
    assert allowed.decision is InvocationDecision.ALLOWED
    rejected = invoker.invoke(
        "agent.remediate",
        caller_run_id="run-caller",
        depth=2,
        chain=["triage", "other"],
    )
    assert rejected.decision is InvocationDecision.REFUSED
    assert "depth limit" in (rejected.reason or "")
    assert len(runs.submissions) == 1


def test_cycle_in_chain_is_rejected() -> None:
    invoker, runs, _ = _invoker()
    invocation = invoker.invoke(
        "agent.triage", caller_run_id="run-caller", depth=1, chain=["triage"]
    )
    assert invocation.decision is InvocationDecision.REFUSED
    assert "cycle" in (invocation.reason or "")
    assert runs.submissions == []


def test_budget_exhaustion_is_rejected() -> None:
    invoker, runs, _ = _invoker()
    invocation = invoker.invoke(
        "agent.triage",
        caller_run_id="run-caller",
        budget_remaining_usd=1.0,
        caller_cost_usd=1.0,
    )
    assert invocation.decision is InvocationDecision.REFUSED
    assert "budget" in (invocation.reason or "")
    assert runs.submissions == []


def test_policy_refusal_at_nested_admission_is_recorded() -> None:
    invoker, runs, _ = _invoker(refuse=True)
    invocation = invoker.invoke("agent.triage", caller_run_id="run-caller")
    assert invocation.decision is InvocationDecision.REFUSED
    assert "admission refused" in (invocation.reason or "")
    assert len(runs.submissions) == 1


def test_invocations_are_recorded() -> None:
    store = InMemoryAgentToolStore()
    invoker, _, _ = _invoker(store=store)
    invocation = invoker.invoke("agent.triage", caller_run_id="run-caller")
    stored = store.get_invocation(invocation.invocation_id)
    assert stored is not None
    assert stored.workload == "triage"
    assert [item.invocation_id for item in store.list_invocations(caller_run_id="run-caller")] == [
        invocation.invocation_id
    ]


def test_invocation_is_deterministic() -> None:
    invoker, _, _ = _invoker()
    first = invoker.invoke("agent.triage", caller_run_id="c")
    second = invoker.invoke("agent.triage", caller_run_id="c")
    assert first.decision == second.decision
    assert first.chain == second.chain


def test_max_depth_must_be_positive() -> None:
    registry = AgentToolRegistry(FakeRegistry({"triage": "t"}))
    with pytest.raises(ValueError):
        AgentToolInvoker(registry, FakeRunService(), max_depth=0)


def test_max_depth_property() -> None:
    invoker, _, _ = _invoker(max_depth=3)
    assert invoker.max_depth == 3


def test_refused_error_carries_reason() -> None:
    error = AgentToolRefusedError("agent.triage", "not certified")
    assert error.tool_id == "agent.triage"
    assert error.reason == "not certified"
    assert "refused" in str(error)
