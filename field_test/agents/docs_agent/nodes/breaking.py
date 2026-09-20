from __future__ import annotations
import logging

from llm.client import LLMClient
from state import AgentState, BreakingChange
from tools.breaking_detector import detect_breaking_change, propose_migration_text

logger = logging.getLogger(__name__)


def breaking_sub(state: AgentState) -> dict:
    commits = state.get("commits", [])
    if not commits:
        logger.info("No commits to check for breaking changes")
        return {"breaking_changes": []}

    breaking_changes: list[BreakingChange] = []
    llm = LLMClient()

    for c in commits:
        result = detect_breaking_change(c)
        if result is not None:
            migration = propose_migration_text(result, c, llm)
            result["proposed_migration"] = migration
            breaking_changes.append(result)

    logger.info("Detected %d breaking changes", len(breaking_changes))
    return {"breaking_changes": breaking_changes}
