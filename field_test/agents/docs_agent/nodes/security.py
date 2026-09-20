from __future__ import annotations
import logging

from llm.client import LLMClient
from state import AgentState, SecurityFix
from tools.osv_client import OSVClient, extract_cve_references

logger = logging.getLogger(__name__)


def security_sub(state: AgentState) -> dict:
    commits = state.get("commits", [])
    if not commits:
        logger.info("No commits to check for security fixes")
        return {"security_fixes": []}

    security_fixes: list[SecurityFix] = []
    llm = LLMClient()
    osv = OSVClient()

    try:
        seen_cves: set[str] = set()
        for c in commits:
            cve_ids = extract_cve_references(c.get("raw_message", ""))
            scope = c.get("scope", "") or ""
            uses_security_scope = "security" in scope.lower() or "sec" in scope.lower()

            if not cve_ids and not uses_security_scope:
                continue

            unique_cve_ids = [c for c in cve_ids if c not in seen_cves]
            seen_cves.update(unique_cve_ids)

            if not unique_cve_ids:
                security_fixes.append(SecurityFix(
                    commit_sha=c["sha"],
                    cve_ids=[],
                    cvss_score=None,
                    affected_versions="",
                    advisory_draft=f"Security-related commit (scope: {scope}).",
                    disclosure="immediate",
                    human_edited=False,
                ))
                continue

            for cve_id in unique_cve_ids:
                cve_data = osv.query_cve(cve_id)
                if cve_data is None:
                    security_fixes.append(SecurityFix(
                        commit_sha=c["sha"],
                        cve_ids=[cve_id],
                        cvss_score=None,
                        affected_versions="",
                        advisory_draft=f"Security fix referenced in commit {c['sha'][:8]}.",
                        disclosure="immediate",
                        human_edited=False,
                    ))
                    continue

                cvss_score = _parse_cvss(cve_data)
                advisory = osv.draft_advisory(cve_data, llm)
                affected_versions = _format_affected(cve_data.get("affected", []))

                security_fixes.append(SecurityFix(
                    commit_sha=c["sha"],
                    cve_ids=[cve_id],
                    cvss_score=cvss_score,
                    affected_versions=affected_versions,
                    advisory_draft=advisory,
                    disclosure="immediate",
                    human_edited=False,
                ))
    finally:
        osv.close()

    logger.info("Found %d security fixes", len(security_fixes))
    return {"security_fixes": security_fixes}


def _parse_cvss(cve_data: dict) -> float | None:
    severity = cve_data.get("severity", "")
    if isinstance(severity, (int, float)):
        return float(severity)
    cvss_map = {"critical": 9.5, "high": 7.5, "medium": 5.0, "low": 2.5}
    return cvss_map.get(severity.lower())


def _format_affected(affected: list[dict]) -> str:
    parts = []
    for a in affected:
        pkg = a.get("package", {})
        name = pkg.get("name", "unknown")
        ecosystem = pkg.get("ecosystem", "")
        ranges = a.get("ranges", [])
        if ranges:
            for r in ranges:
                fixed_version = "latest"
                for event in r.get("events", []):
                    if "fixed" in event:
                        fixed_version = event["fixed"]
                for event in r.get("events", []):
                    if "introduced" in event:
                        parts.append(f"{name} ({ecosystem}) < {fixed_version}")
                        break
        else:
            parts.append(f"{name} ({ecosystem})")
    return "; ".join(parts) if parts else "unknown"
