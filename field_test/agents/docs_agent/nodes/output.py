from __future__ import annotations
import logging
import os

from tools.drafter import format_output
from state import AgentState

logger = logging.getLogger(__name__)


def write_output(state: AgentState) -> dict:
    repo = state["repo"]
    base = state["base_tag"]
    head = state["head_tag"]
    draft = state.get("draft_notes", "")
    semver = state.get("semver_proposal")

    output_dir = os.environ.get("OUTPUT_DIR", "output")
    repo_dir = repo.replace("/", "-")
    dest_dir = os.path.join(output_dir, repo_dir)
    os.makedirs(dest_dir, exist_ok=True)

    safe_base = base.replace("/", "-")
    safe_head = head.replace("/", "-")
    filename = f"{safe_base}...{safe_head}.md"
    dest_path = os.path.join(dest_dir, filename)

    formatted = format_output(draft, semver, repo, base, head)

    try:
        with open(dest_path, "w") as f:
            f.write(formatted)
    except OSError as e:
        msg = f"Cannot write output to {dest_path}. Check permissions and disk space."
        logger.error("%s: %s", msg, e)
        return {"errors": [msg]}

    logger.info("Output written to %s", dest_path)
    return {}
