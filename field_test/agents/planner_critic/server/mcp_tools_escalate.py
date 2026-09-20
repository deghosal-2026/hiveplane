"""Escalation MCP tools (F-32): escalate_list, escalate_approve, escalate_deny.

Each tool is a standalone function that opens the plan store, drives the
escalation manager, and returns structured output. The MCP server (M5) will
wrap these into tool definitions for the Model Context Protocol. Until the
server exists, the tools are independently testable.
"""

from __future__ import annotations

import json
from typing import Literal, cast

from ..escalation import EscalationManager, build_escalation_manager
from ..roles import CriticRole
from ..schema.plan import PlanVersion
from ..store.sqlite import SQLiteStore
from ..types import Finding


def _open_manager(
    store_path: str,
    escalation_id: str | None = None,
) -> tuple[SQLiteStore, EscalationManager]:
    """Open a store and create a manager with authority pre-bound.

    Args:
        store_path: Path to the SQLite plan store.
        escalation_id: Optional escalation id to look up approving_authority.

    Returns:
        (store, manager) — caller must close the store.
    """
    store = SQLiteStore(store_path)
    if escalation_id is not None:
        return store, build_escalation_manager(store, escalation_id=escalation_id)
    return store, EscalationManager(store)


class _GateOnlyCritic(CriticRole):
    """A critic that surfaces only deterministic-gate findings."""

    def audit(self, plan: PlanVersion, findings: list[Finding]) -> list[Finding]:
        return list(findings)


def escalate_list(
    store_path: str,
    status: str | None = None,
) -> list[dict[str, object]]:
    """List escalations.

    Args:
        store_path: Path to the SQLite plan store.
        status: Filter by status (``open``, ``approved``, ``denied``); None for all.

    Returns:
        A list of escalation dictionaries.
    """
    store, manager = _open_manager(store_path)
    try:
        typed = cast("Literal['open', 'approved', 'denied'] | None", status)
        return [e.model_dump(mode="json") for e in manager.list_escalations(status=typed)]
    finally:
        store.close()


def escalate_approve(
    store_path: str,
    escalation_id: str,
    note: str = "",
    patch_json: str | None = None,
    principal: str | None = None,
) -> dict[str, object]:
    """Approve an escalation, optionally patching the plan first.

    Args:
        store_path: Path to the SQLite plan store.
        escalation_id: The escalation to approve.
        note: Optional resolution note.
        patch_json: Optional PlanVersion JSON to store and re-critique.
        principal: Approving principal (required when approving_authority is set).

    Returns:
        The resolved escalation dictionary.
    """
    store, manager = _open_manager(store_path, escalation_id=escalation_id)
    try:
        if patch_json is not None:
            patch = PlanVersion.from_dict(json.loads(patch_json))
            manager.patch_and_recritique(plan_id=patch.id, patch=patch, critic=_GateOnlyCritic())
        resolved = manager.resolve(escalation_id, "approved", note=note, principal=principal)
        return resolved.model_dump(mode="json")
    finally:
        store.close()


def escalate_deny(
    store_path: str,
    escalation_id: str,
    note: str = "",
    principal: str | None = None,
) -> dict[str, object]:
    """Deny an escalation.

    Args:
        store_path: Path to the SQLite plan store.
        escalation_id: The escalation to deny.
        note: Optional resolution note.
        principal: Denying principal (required when approving_authority is set).

    Returns:
        The resolved escalation dictionary.
    """
    store, manager = _open_manager(store_path, escalation_id=escalation_id)
    try:
        resolved = manager.resolve(escalation_id, "denied", note=note, principal=principal)
        return resolved.model_dump(mode="json")
    finally:
        store.close()


__all__ = ["escalate_approve", "escalate_deny", "escalate_list"]
