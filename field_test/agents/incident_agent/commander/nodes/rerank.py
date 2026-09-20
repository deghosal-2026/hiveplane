"""Evidence reranking — reorder retrieved evidence by relevance to the incident.

The reranker combines RAG results with timeline events and prioritises
evidence that matches the incident's service, shares keyword overlap,
or correlates with a recent deploy.
"""

from __future__ import annotations

from typing import Any

from incident_commander.models.state import IncidentState


def _score_evidence(
    item: dict[str, Any],
    service: str,
    deploy_keywords: set[str],
) -> float:
    """Compute a relevance score for a single evidence item.

    Factors (higher = more relevant):
    - ``+3`` if the item's service matches the incident service
    - ``+2`` per deploy-related keyword found in the item
    - ``+1`` for each ``deploy_correlation`` flag on timeline-originating items

    The weights are chosen so that a service match outweighs any number of
    keyword hits, making the reranker service-aware first, content-aware second.
    """
    score = 0.0

    item_svc = item.get("service", "")
    if item_svc == service or item_svc == "*":
        score += 3.0

    item_kw = set(str(k).lower() for k in item.get("keywords", []))
    overlap = len(deploy_keywords & item_kw)
    score += 2.0 * overlap

    if item.get("deploy_correlation", False):
        score += 1.0

    return score


def rerank_evidence_node(state: IncidentState) -> IncidentState:
    """Rerank all retrieved evidence by relevance to the incident.

    Combines ``state.retrieved_runbooks`` and ``state.retrieved_incidents``
    into a single sorted list stored in ``state.reranked_evidence``.

    Reranking favours evidence that:
    - Matches the incident's ``service``
    - Shares keywords with deploy events
    - Comes from a deploy-correlated source
    """
    service = state.service

    # Collect deploy-related keywords from timeline events flagged
    # as deploy-correlated by the deploy_correlation node.
    # These keywords boost evidence that mentions the same deploy topics,
    # creating a feedback loop between correlation and reranking.
    deploy_keywords: set[str] = set()
    for event in state.timeline:
        if event.deploy_correlation:
            deploy_keywords.update(event.content.lower().split())

    # Merge runbook and past-incident evidence into a single pool
    evidence: list[dict[str, Any]] = []
    evidence.extend(state.retrieved_runbooks)
    evidence.extend(state.retrieved_incidents)

    if not evidence:
        state.reranked_evidence = []
        return state

    # Score each evidence item and sort by relevance (highest first)
    scored = [
        (item, _score_evidence(item, service, deploy_keywords))
        for item in evidence
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    state.reranked_evidence = [item for item, _ in scored]
    return state
