"""RAG retrieval: in-memory and Qdrant retrievers for runbooks and past incidents.

Each retriever implements the ``Retriever`` protocol and returns ranked
results with citations suitable for the incident-commander graph nodes.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from incident_commander.models.input import Runbook
from incident_commander.models.state import IncidentState

logger = logging.getLogger(__name__)

# Maximum number of results returned by the retriever
_MAX_RESULTS = 5
# Character limit for content snippets returned to the LLM
_SNIPPET_CHARS = 300


@runtime_checkable
class Retriever(Protocol):
    """Protocol that all retrievers must satisfy."""

    def index(self, runbooks: list[Runbook]) -> None:
        """Index a list of runbooks for retrieval."""

    def index_past_incidents(self, incidents: list[dict[str, Any]]) -> None:
        """Index past incident records for retrieval."""

    def query_runbooks(
        self, service: str, symptoms: str
    ) -> list[dict[str, Any]]:
        """Return ranked runbook results matching the given context."""

    def query_past_incidents(
        self, service: str, symptoms: str
    ) -> list[dict[str, Any]]:
        """Return ranked past incident results matching the given context."""


def _keyword_overlap(query_tokens: set[str], keywords: list[str]) -> int:
    """Count how many query tokens appear in the keyword list."""
    # Normalise keyword hyphens to spaces so "high-cpu" matches "high cpu".
    # This is a lightweight alternative to stemming or lemmatization and
    # handles the most common case of compound-keyword mismatch.
    kw_set = {t for k in keywords for t in k.lower().replace("-", " ").split()}
    return sum(1 for t in query_tokens if t in kw_set)


def _service_match(query_service: str, entry_service: str) -> bool:
    """Exact or wildcard service match."""
    # Wildcard "*" matches any service — useful for runbooks scoped to
    # all services (e.g., generic escalation procedures).
    return query_service == entry_service or entry_service == "*"


def _build_result(
    entry: Runbook | dict[str, Any],
    score: float,
    snippet: str,
    result_type: str,
) -> dict[str, Any]:
    """Build a standard result dict for the retriever return type."""
    if isinstance(entry, Runbook):
        return {
            "title": entry.title,
            "path": entry.path,
            "content_snippet": snippet,
            "keywords": entry.keywords,
            "service": entry.service,
            "citation": f"{entry.title} ({entry.path})",
            "relevance_score": round(score, 3),
            "type": result_type,
        }
    return {
        "title": entry.get("summary", "Unknown"),
        "path": "",
        "content_snippet": snippet,
        "keywords": entry.get("keywords", []),
        "service": entry.get("service", ""),
        # Past incident IDs already include the "INC-" prefix
        "citation": entry.get("id", "unknown"),
        "relevance_score": round(score, 3),
        "type": result_type,
    }


class InMemoryRetriever:
    """Simple keyword-matching retriever that indexes runbooks in memory.

    This is the default retriever.  It does not require any external
    services (no Qdrant, no vector DB).
    """

    def __init__(self) -> None:
        """Initialize with empty runbook and past-incident indexes."""
        self._runbooks: list[Runbook] = []
        self._past_incidents: list[dict[str, Any]] = []

    def index(self, runbooks: list[Runbook]) -> None:
        """Replace the current runbook index with the given list."""
        # Full replacement rather than incremental: the runbook set is
        # small (<1000 entries) and reindexing is cheap.
        self._runbooks = list(runbooks)

    def index_past_incidents(self, incidents: list[dict[str, Any]]) -> None:
        """Replace the current past-incident index."""
        self._past_incidents = list(incidents)

    def _rank(
        self,
        query_service: str,
        query_tokens: set[str],
        entries: list[Runbook] | list[dict[str, Any]],
        type_label: str,
    ) -> list[dict[str, Any]]:
        """Score and rank entries by keyword overlap + service match.

        Scoring: service match (+2) is weighted higher than keyword
        overlap to ensure service-specific runbooks rank before
        cross-service matches with more keyword hits.
        """
        scored: list[tuple[float, Any]] = []
        for entry in entries:
            service = entry.service if isinstance(entry, Runbook) else entry.get("service", "")
            keywords = entry.keywords if isinstance(entry, Runbook) else entry.get("keywords", [])
            overlap = _keyword_overlap(query_tokens, keywords)
            svc = _service_match(query_service, service)
            # Service match (+2) outweighs keyword overlap to prefer
            # service-specific results over generic keyword matches.
            # E.g. a "payment" runbook scores 2+overlap for a payment-service
            # incident, while a generic "timeout" runbook scores only overlap.
            score = overlap + (2.0 if svc else 0.0)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for score, entry in scored[:_MAX_RESULTS]:
            content = entry.content if isinstance(entry, Runbook) else entry.get("summary", "")
            # Truncate long content with ellipsis to keep prompts small
            snippet = content[:_SNIPPET_CHARS] + ("..." if len(content) > _SNIPPET_CHARS else "")
            results.append(_build_result(entry, score, snippet, type_label))
        return results

    def query_runbooks(
        self, service: str, symptoms: str
    ) -> list[dict[str, Any]]:
        """Return ranked runbooks matching the incident context."""
        tokens = set(symptoms.lower().split())
        return self._rank(service, tokens, self._runbooks, "runbook")

    def query_past_incidents(
        self, service: str, symptoms: str
    ) -> list[dict[str, Any]]:
        """Return ranked past incidents matching the incident context."""
        tokens = set(symptoms.lower().split())
        return self._rank(service, tokens, self._past_incidents, "past_incident")


# Module-level retriever — initialized once by the graph builder.
# Falls back to a default InMemoryRetriever when not explicitly set.
_retriever: Retriever = InMemoryRetriever()


def init_retriever(retriever: Retriever, runbooks: list[Runbook] | None = None) -> None:
    """Set the module-level retriever and optionally index runbooks.

    Called once during graph construction (before the first node runs).
    """
    global _retriever  # noqa: PLW0603
    _retriever = retriever
    if runbooks:
        _retriever.index(runbooks)


def retrieve_runbooks_node(state: IncidentState) -> IncidentState:
    """Query the module-level retriever and store results in state.

    Results are written to ``state.retrieved_runbooks`` and
    ``state.retrieved_incidents``.
    """
    # Build query from the last 5 timeline events (most recent context).
    # If timeline is empty, fall back to the alert summary.
    # This sliding window prevents the query from including stale events
    # as the incident progresses through multiple update cycles.
    symptoms = (
        " ".join(e.content for e in state.timeline[-5:])
        if state.timeline
        else (state.alert.summary if state.alert else "")
    )

    try:
        state.retrieved_runbooks = _retriever.query_runbooks(state.service, symptoms)
    except Exception:
        logger.warning("Runbook query failed", exc_info=True)
        state.retrieved_runbooks = []

    try:
        state.retrieved_incidents = _retriever.query_past_incidents(state.service, symptoms)
    except Exception:
        logger.warning("Past incident query failed", exc_info=True)
        state.retrieved_incidents = []

    return state
