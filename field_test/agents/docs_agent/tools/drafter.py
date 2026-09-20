from __future__ import annotations
import logging
from datetime import datetime, timezone

from llm.client import LLMClient
from state import ClassifiedCommit, BreakingChange, SecurityFix, ToneExample, SemverProposal

logger = logging.getLogger(__name__)

SECTION_ORDER = ["Highlights", "Breaking Changes", "Features", "Fixes", "Security", "Other"]

MERGE_PATTERNS = ("merge pull request", "merge branch", "merge remote-tracking branch")


def _build_prompt(
    classified: list[ClassifiedCommit],
    breaking: list[BreakingChange],
    security: list[SecurityFix],
    tone_examples: list[ToneExample],
) -> tuple[str, str]:
    tone_section = ""
    if tone_examples:
        tone_section = "\n\n--- Tone Examples (match this style) ---\n"
        for ex in tone_examples:
            tone_section += f"\n--- {ex['source']} ---\n{ex['text'][:2000]}\n"

    commits_section = ""
    for c in classified:
        commit = c["commit"]
        scope = commit.get("scope")
        scope_part = f"({scope})" if scope else ""
        commits_section += (
            f"- {commit['sha'][:8]} | {commit['type'].value}"
            f"{scope_part}"
            f": {commit['description']}"
            f" | notable={c['is_notable']} confidence={c['confidence']}\n"
        )

    breaking_section = ""
    for b in breaking:
        breaking_section += (
            f"- {b['commit_sha'][:8]} scope={b.get('scope', 'N/A')}"
            f" severity={b.get('severity', 'N/A')}"
            f" migration={b.get('human_migration_text', b.get('proposed_migration', 'N/A'))}\n"
        )

    security_section = ""
    for s in security:
        security_section += (
            f"- {s['commit_sha'][:8]} CVEs={', '.join(s.get('cve_ids', []))}"
            f" advisory={s.get('human_advisory_text', s.get('advisory_draft', 'N/A'))}\n"
        )

    system = (
        "You are a release notes drafter. Given classified commits, breaking changes, security fixes, "
        "and tone examples from past releases, produce release notes in the repository's style.\n\n"
        "Output JSON with a single field 'sections' mapping section names to markdown text.\n"
        f"Sections must be exactly: {SECTION_ORDER}\n"
        "Each section should contain user-facing prose (not just bullet lists). "
        "Include commit SHA links and PR references. "
        "For Breaking Changes, include migration guidance. "
        "For Security, include advisory text with CVE IDs."
    )

    user = (
        f"Tone examples:{tone_section}\n\n"
        f"--- Classified Commits ---\n{commits_section}\n"
        f"--- Breaking Changes ---\n{breaking_section}\n"
        f"--- Security Fixes ---\n{security_section}\n"
        "Produce release notes matching the repo's established tone from the examples."
    )

    return system, user


def draft_release_notes(
    classified: list[ClassifiedCommit],
    breaking: list[BreakingChange],
    security: list[SecurityFix],
    tone_examples: list[ToneExample],
    llm: LLMClient | None = None,
) -> str:
    if llm is None:
        llm = LLMClient()

    system, user = _build_prompt(classified, breaking, security, tone_examples)

    try:
        result = llm.complete_structured(
            system_prompt=system,
            user_prompt=user,
            max_tokens=4096,
            temperature=0.3,
        )
    except Exception as e:
        logger.error("LLM drafting failed: %s", e)
        return _fallback_draft(classified, breaking, security)

    sections = result.get("sections", {}) if isinstance(result, dict) else {}
    parts: list[str] = []
    for section_name in SECTION_ORDER:
        if isinstance(sections, list):
            content = ""
        else:
            content = sections.get(section_name, "")
        if isinstance(content, list):
            content = "\n\n".join(str(item) for item in content)
        if content:
            parts.append(f"## {section_name}\n\n{content.strip()}")

    if not parts:
        logger.warning("LLM returned empty sections, using fallback")
        return _fallback_draft(classified, breaking, security)

    return "\n\n---\n\n".join(parts)


def _is_merge_commit(commit: ClassifiedCommit) -> bool:
    desc = commit["commit"]["description"].lower()
    return any(desc.startswith(p) for p in MERGE_PATTERNS)


def _fallback_draft(
    classified: list[ClassifiedCommit],
    breaking: list[BreakingChange],
    security: list[SecurityFix],
) -> str:
    parts: list[str] = []

    notable_commits = [c for c in classified if c["is_notable"] and not _is_merge_commit(c)]
    if notable_commits:
        h_section = "## Highlights\n\n"
        top = sorted(notable_commits, key=lambda c: c["confidence"], reverse=True)[:5]
        for c in top:
            h_section += f"- {c['commit']['description']}\n"
        parts.append(h_section)

    if breaking:
        b_section = "## Breaking Changes\n\n"
        for b in breaking:
            footer = b.get("footer_text", "")
            migration = b.get("human_migration_text") or b.get("proposed_migration", "")
            if footer and "unavailable" not in migration.lower():
                b_section += f"- **{b.get('commit_sha', 'unknown')[:8]}**: {migration}\n"
            elif footer:
                b_section += f"- **{b.get('commit_sha', 'unknown')[:8]}**: {footer}\n"
            elif "unavailable" in migration.lower():
                b_section += f"- **{b.get('commit_sha', 'unknown')[:8]}**: {migration}\n"
            else:
                b_section += f"- **{b.get('commit_sha', 'unknown')[:8]}**: {migration}\n"
        parts.append(b_section)

    feat_group = [c for c in classified if c["commit"]["type"].value == "feat" and not _is_merge_commit(c)]
    if feat_group:
        f_section = "## Features\n\n"
        for c in feat_group:
            f_section += f"- {c['commit']['description']}\n"
        parts.append(f_section)

    fix_group = [c for c in classified if c["commit"]["type"].value == "fix" and not _is_merge_commit(c)]
    if fix_group:
        fix_section = "## Fixes\n\n"
        for c in fix_group:
            fix_section += f"- {c['commit']['description']}\n"
        parts.append(fix_section)

    if security:
        s_section = "## Security\n\n"
        grouped: dict[str, list[SecurityFix]] = {}
        for s in security:
            sha = s.get("commit_sha", "unknown")
            grouped.setdefault(sha, []).append(s)
        for sha, fixes in grouped.items():
            all_cves: list[str] = []
            advisories: list[str] = []
            for s in fixes:
                cves = s.get("cve_ids", [])
                all_cves.extend(cves)
                advisory = s.get("human_advisory_text") or s.get("advisory_draft", "")
                if advisory:
                    advisories.append(advisory)
            if all_cves:
                cve_str = ", ".join(all_cves)
                advisory_text = advisories[0] if advisories else f"Security fix referenced in commit {sha[:8]}."
                s_section += f"- **{cve_str}**: {advisory_text}\n"
            else:
                advisory_text = advisories[0] if advisories else f"Security-related commit ({sha[:8]})."
                s_section += f"- **{sha[:8]}**: {advisory_text}\n"
        parts.append(s_section)

    notable_types = {"feat", "fix"}
    merge_count = sum(1 for c in classified if _is_merge_commit(c))
    other = [
        c for c in classified
        if c["commit"]["type"].value not in notable_types
        and not _is_merge_commit(c)
        and c["is_notable"]
    ]
    if other:
        o_section = "## Other\n\n"
        for c in other:
            o_section += f"- {c['commit']['description']}\n"
        parts.append(o_section)

    if merge_count:
        logger.info("Excluded %d merge commits from output", merge_count)

    return "\n\n---\n\n".join(parts) if parts else "No changes in this release."


def format_output(
    draft: str,
    semver: SemverProposal | None,
    repo: str,
    base: str,
    head: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    semver_banner = ""
    if semver:
        semver_banner = (
            f"**Proposed Semver:** {semver['proposed_version']} "
            f"(bump: {semver['bump_type']}) — {semver['justification']}\n"
        )

    return (
        f"# Release Notes — {repo}\n\n"
        f"**Range:** `{base}` → `{head}`\n"
        f"**Date:** {now}\n\n"
        f"{semver_banner}"
        f"---\n\n"
        f"{draft}\n"
    )
