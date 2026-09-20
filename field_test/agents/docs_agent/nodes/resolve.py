from __future__ import annotations
import logging

from state import AgentState

logger = logging.getLogger(__name__)


def resolve_breaking(state: AgentState) -> dict:
    breaking = state.get("breaking_changes", [])
    index = state.get("current_interrupt_index", 0)
    logger.info("Resolved breaking change %d of %d", index, len(breaking))
    if index > 0 and index <= len(breaking):
        item = breaking[index - 1]
        if item.get("human_migration_text"):
            item["human_edited"] = True
    return {"breaking_changes": breaking}


def resolve_security(state: AgentState) -> dict:
    security = state.get("security_fixes", [])
    logger.info("Resolved %d security fixes", len(security))
    for fix in security:
        if fix.get("human_advisory_text"):
            fix["human_edited"] = True
    return {"security_fixes": security}
