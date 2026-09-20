from __future__ import annotations
import logging

from tools.semver import propose_semver
from state import AgentState

logger = logging.getLogger(__name__)


def propose_semver_node(state: AgentState) -> dict:
    current_version = state.get("base_tag", "")
    classified = state.get("classified_commits", [])
    breaking = state.get("breaking_changes", [])
    tag_convention = state.get("repo_tag_convention", "semver")
    override = state.get("semver_override")

    if override:
        current_version = override

    proposal = propose_semver(current_version, classified, breaking, tag_convention, head_tag=state.get("head_tag", ""))

    logger.info(
        "Semver proposal: %s -> %s (%s)",
        current_version, proposal["proposed_version"], proposal["bump_type"],
    )
    return {"semver_proposal": proposal}
