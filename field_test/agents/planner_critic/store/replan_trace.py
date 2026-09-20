"""Replan trace (F-53): record sub-plan linkage after a replan.

When a plan fails mid-execution and is replanned (patch or restart), a
:class:`ReplanLink` records the binding between the new revision and its
parent, along with the replan policy and an optional snapshot of what
completed before the failure. The full lineage — original → partial execution
→ replan → completion — is traversable through the store's
``get_replan_link`` / ``get_child_replan_links`` methods.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReplanLink(BaseModel):
    """A record of one replan: the child revision linked to its parent."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="The plan id (shared across revisions)")
    version: int = Field(ge=1, description="The replan revision number")
    parent_plan_id: str = Field(description="The parent plan's id")
    parent_version: int = Field(ge=1, description="The parent plan's version")
    policy: Literal["patch", "restart"] = Field(
        description="The replan policy that produced this revision"
    )
    partial_execution: str | None = Field(
        default=None,
        description="Optional JSON snapshot of execution state before the replan",
    )


__all__ = ["ReplanLink"]
