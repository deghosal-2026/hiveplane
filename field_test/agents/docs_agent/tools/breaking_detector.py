from __future__ import annotations
import logging
import re
from typing import Any

from state import ConventionalCommit, BreakingChange

logger = logging.getLogger(__name__)


def detect_breaking_change(commit: ConventionalCommit) -> BreakingChange | None:
    if not commit["is_breaking"]:
        return None

    severity = _infer_severity(commit)
    return BreakingChange(
        commit_sha=commit["sha"],
        scope=commit["scope"],
        footer_text=commit.get("breaking_footer") or "",
        proposed_migration="",
        severity=severity,
        disclosure="immediate",
        human_edited=False,
    )


def _infer_severity(commit: ConventionalCommit) -> str:
    footer = (commit.get("breaking_footer") or "").lower()
    description = commit["description"].lower()
    combined = f"{footer} {description}"
    words = set(re.sub(r'[^\w\s]', ' ', combined).split())
    if any(w in words for w in ["remove", "removes", "removed", "removing", "drop", "drops", "dropped", "dropping", "delete", "deletes", "deleted", "deleting", "deprecate", "deprecates", "deprecated"]):
        return "removed"
    if any(w in words for w in ["change", "changes", "changed", "changing", "modify", "modifies", "modified", "modifying", "update", "updates", "updated", "updating", "rename", "renames", "renamed", "renaming"]):
        return "breaking"
    return "migration"


def propose_migration_text(
    breaking: BreakingChange,
    commit: ConventionalCommit,
    llm_client: Any,
) -> str:
    prompt = (
        f"Commit: {commit['sha']}\n"
        f"Scope: {commit['scope']}\n"
        f"Description: {commit['description']}\n"
        f"Breaking footer: {commit.get('breaking_footer', 'N/A')}\n"
        f"Body: {commit.get('body', 'N/A')}\n\n"
        "Write migration guidance for users affected by this breaking change. "
        "Explain what changed, why, and how to update their code."
    )
    try:
        return llm_client.complete(
            system_prompt=(
                "You are a migration guide writer for open-source projects. "
                "Given a breaking change commit, write clear migration guidance."
            ),
            user_prompt=prompt,
            max_tokens=512,
        )
    except Exception as e:
        logger.warning("Migration text generation failed: %s", e)
        footer = commit.get("breaking_footer") or ""
        desc = commit.get("description") or ""
        if footer:
            return f"{desc}. Breaking change: {footer}. Migration guidance pending — see commit {commit['sha'][:8]} for details."
        return f"{desc}. Migration guidance pending — see commit {commit['sha'][:8]} for details."
