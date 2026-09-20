from __future__ import annotations
import logging

from llm.client import LLMClient
from state import AgentState
from tools.classifier import classify_commits

logger = logging.getLogger(__name__)


def classify_sub(state: AgentState) -> dict:
    commits = state.get("commits", [])
    if not commits:
        logger.info("No commits to classify")
        return {"classified_commits": []}

    import sys
    print(f"  Classifying {len(commits)} commits...", file=sys.stderr, flush=True)
    llm = LLMClient()
    classified = classify_commits(commits, llm)

    notable_count = sum(1 for c in classified if c["is_notable"])
    logger.info(
        "Classified %d commits: %d notable, %d trivial",
        len(classified), notable_count, len(classified) - notable_count,
    )

    return {"classified_commits": classified}
