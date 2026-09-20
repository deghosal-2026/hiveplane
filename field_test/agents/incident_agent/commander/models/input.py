"""Input models: Runbook, IncidentMeta, IncidentInput.

ChatMessage, LogEntry, and GitHubPR are defined in ``state.py`` to avoid
a circular import.  They are re-exported from this module via the
package ``__init__.py`` for convenience.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .state import Alert, ChatMessage, GitHubPR, LogEntry, TimelineEvent


class Runbook(BaseModel):
    """A runbook for incident response — indexed by keywords for RAG retrieval.

    The empty-string defaults for id/path/service mean they are optional
    in the input JSON; the RAG retriever treats empty service as
    non-matching (never returns it as service-specific).
    """

    id: str = ""
    title: str
    path: str = ""
    content: str
    keywords: list[str] = Field(default_factory=list)
    service: str = ""


class IncidentMeta(BaseModel):
    """Incident metadata from meta.json — identifies and contextualizes the incident."""

    incident_id: str
    service: str
    severity: Literal["SEV1", "SEV2", "SEV3"]
    start_time: datetime
    description: str = ""
    commander: str = ""  # person leading the incident response
    oncall_roster: list[str] = Field(default_factory=list)  # engineers on call for escalation
    tags: list[str] = Field(default_factory=list)  # free-form labels for search/grouping


class IncidentInput(BaseModel):
    """Aggregate input for run_incident() — all data channels in one object.

    Only ``alert`` is required; all other channels default to empty lists.
    """

    schema_version: str = "0.1.0"  # input format version for forward-compat checks
    alert: Alert  # the triggering alert — only required field
    logs: list[LogEntry] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)  # chat history channel
    github: list[GitHubPR] = Field(default_factory=list)  # recently merged PRs channel
    runbooks: list[Runbook] = Field(default_factory=list)  # runbooks to index for RAG
    manual_events: list[TimelineEvent] = Field(default_factory=list)  # human-supplied events
    meta: IncidentMeta | None = None  # optional metadata from meta.json
