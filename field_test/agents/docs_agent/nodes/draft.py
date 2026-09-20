from __future__ import annotations
import logging

from tools.drafter import draft_release_notes
from state import AgentState

logger = logging.getLogger(__name__)


def draft_notes(state: AgentState) -> dict:
    classified = state.get("classified_commits", [])
    breaking = state.get("breaking_changes", [])
    security = state.get("security_fixes", [])
    tone_examples = state.get("tone_examples", [])

    import sys
    print(f"  Drafting release notes ({len(classified)} commits, {len(breaking)} breaking, {len(security)} security)...", file=sys.stderr, flush=True)
    draft = draft_release_notes(classified, breaking, security, tone_examples)

    logger.info(
        "Drafted release notes: %d classified, %d breaking, %d security fixes",
        len(classified), len(breaking), len(security),
    )
    return {"draft_notes": draft}
