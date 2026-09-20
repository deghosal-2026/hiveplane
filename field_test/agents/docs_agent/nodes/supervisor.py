from __future__ import annotations
import logging

from llm.client import LLMClient
from state import AgentState, CommitType, RoutingPlan

logger = logging.getLogger(__name__)

SECURITY_PRIORITY = 1
BREAKING_PRIORITY = 2
CLASSIFY_PRIORITY = 3


def _llm_routing_fallback(commit, llm: LLMClient) -> str | None:
    try:
        result = llm.complete_structured(
            system_prompt=(
                "You are a commit router. Determine which sub-agent should handle "
                "this commit. Return a JSON object with 'sub_agent' field. "
                "Options: 'security_sub' (if CVE or security scope), "
                "'breaking_sub' (if breaking change), 'classify_sub' (otherwise)."
            ),
            user_prompt=(
                f"sha={commit['sha']} type={commit['type'].value} "
                f"scope={commit['scope']} desc={commit['description']} "
                f"is_breaking={commit['is_breaking']}"
            ),
        )
        sub_agent = result.get("sub_agent", "")
        if sub_agent in ("security_sub", "breaking_sub", "classify_sub"):
            return sub_agent
    except Exception:
        logger.warning("LLM routing fallback failed for commit %s", commit["sha"])
    return None


def router_supervisor(state: AgentState) -> dict:
    commits = state.get("commits", [])
    if not commits:
        return {"pending_routing": []}

    llm = LLMClient()
    pending: list[RoutingPlan] = []
    for commit in commits:
        sha = commit["sha"]
        raw_message = commit.get("raw_message", "")
        scope = commit.get("scope", "") or ""
        routes: list[RoutingPlan] = []

        if "CVE-" in raw_message or "security" in scope.lower():
            routes.append({"commit_sha": sha, "sub_agent": "security_sub", "priority": SECURITY_PRIORITY})
        if commit.get("is_breaking", False):
            routes.append({"commit_sha": sha, "sub_agent": "breaking_sub", "priority": BREAKING_PRIORITY})
        if commit["type"] in (CommitType.feat, CommitType.fix, CommitType.refactor):
            routes.append({"commit_sha": sha, "sub_agent": "classify_sub", "priority": CLASSIFY_PRIORITY})
        elif commit["type"] == CommitType.unknown:
            desc = (commit.get("description") or "") + " " + (commit.get("scope") or "")
            kw = {"cve", "security", "vulnerability", "exploit", "xsrf", "xss", "injection", "cve-", "privilege escalation", "overflow", "traversal", "sqli", "rce", "csrf"}
            if any(k in desc.lower() for k in kw):
                if not any(r["sub_agent"] == "security_sub" for r in routes):
                    routes.append({"commit_sha": sha, "sub_agent": "security_sub", "priority": SECURITY_PRIORITY})
                routes.append({"commit_sha": sha, "sub_agent": "classify_sub", "priority": CLASSIFY_PRIORITY})
            else:
                routes.append({"commit_sha": sha, "sub_agent": "classify_sub", "priority": CLASSIFY_PRIORITY})
        else:
            routes.append({"commit_sha": sha, "sub_agent": "classify_sub", "priority": CLASSIFY_PRIORITY})
            routes.append({"commit_sha": sha, "sub_agent": "classify_sub", "priority": CLASSIFY_PRIORITY})

        if not routes:
            routes.append({"commit_sha": sha, "sub_agent": "classify_sub", "priority": CLASSIFY_PRIORITY})

        pending.extend(routes)

    classify_count = sum(1 for p in pending if p["sub_agent"] == "classify_sub")
    breaking_count = sum(1 for p in pending if p["sub_agent"] == "breaking_sub")
    security_count = sum(1 for p in pending if p["sub_agent"] == "security_sub")
    logger.info(
        "Routed %d entries across %d commits: %d classify, %d breaking, %d security",
        len(pending), len(commits), classify_count, breaking_count, security_count,
    )
    return {"pending_routing": pending}


def aggregate_sub_results(state: AgentState) -> dict:
    commits = state.get("commits", [])
    classified = state.get("classified_commits", [])
    breaking = state.get("breaking_changes", [])
    security = state.get("security_fixes", [])
    total_commits = len(commits)

    classified_shas = {c["commit"]["sha"] for c in classified}
    breaking_shas = {b["commit_sha"] for b in breaking}
    security_shas = {s["commit_sha"] for s in security}
    uncovered = [
        c["sha"] for c in commits
        if c["sha"] not in classified_shas
        and c["sha"] not in breaking_shas
        and c["sha"] not in security_shas
    ]

    if uncovered:
        logger.warning(
            "%d commits not covered by any sub-agent: %s",
            len(uncovered), uncovered,
        )

    logger.info(
        "Aggregated: %d classified, %d breaking, %d security fixes (%d commits)",
        len(classified), len(breaking), len(security), total_commits,
    )
    return {}
