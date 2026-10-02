"""Run entrypoint for the `ask` workload (M53-03, dogfooding the thesis).

The control plane binds a tenant-scoped :class:`~hiveplane.ask.service.AskService`
at startup; the `ask` workload then runs through the normal admission, budget, and
policy path like any other certified workload.
"""

from __future__ import annotations

from typing import Any

from hiveplane.ask.service import AskService

_service: AskService | None = None
_operator: str = "ask-workload"
_tenant: str = "default"


def bind(
    service: AskService, *, operator_id: str = "ask-workload", tenant_id: str = "default"
) -> None:
    """Bind the live copilot the workload entrypoint will answer with."""
    global _service, _operator, _tenant
    _service = service
    _operator = operator_id
    _tenant = tenant_id


def run(task: dict[str, Any]) -> dict[str, Any]:
    """Answer a question from the task input; the workload's run body."""
    if _service is None:
        raise RuntimeError("ask workload is not bound to a copilot service")
    question = str(task.get("question", ""))
    return _service.query(
        question, tenant_id=_tenant, operator_id=_operator
    ).model_dump(mode="json")
