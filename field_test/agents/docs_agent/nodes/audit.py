from __future__ import annotations
import json
import logging
import os
import sqlite3

from rich.console import Console
from rich.table import Table

from state import AgentState

logger = logging.getLogger(__name__)
console = Console()


def fetch_releases_node(state: AgentState) -> dict:
    repo = state["repo"]
    token = os.environ.get("GITHUB_TOKEN")
    from tools.github import GitHubClient
    with GitHubClient(token=token) as gh:
        releases = gh.fetch_releases(repo, limit=10)
    logger.info("Fetched %d releases for %s", len(releases), repo)
    return {"releases": releases}


def audit_releases_node(state: AgentState) -> dict:
    releases = state.get("releases", [])
    if not releases:
        return {"errors": ["No releases to audit"]}

    audits = []
    for release in releases:
        body = release.get("body", "") or ""
        tag = release.get("tag_name", "unknown")
        has_sections = any(s in body for s in ["## ", "### ", "**"])
        has_bullets = "  -" in body or "* " in body
        word_count = len(body.split())
        breaking_mentioned = "BREAKING" in body
        cve_mentioned = "CVE-" in body
        completeness = 0.0
        if word_count > 50:
            completeness += 0.4
        if has_sections:
            completeness += 0.3
        if has_bullets:
            completeness += 0.2
        if breaking_mentioned:
            completeness += 0.1
        audits.append({
            "tag": tag,
            "word_count": word_count,
            "has_sections": has_sections,
            "has_bullets": has_bullets,
            "breaking_mentioned": breaking_mentioned,
            "cve_mentioned": cve_mentioned,
            "completeness": min(completeness, 1.0),
        })

    return {"releases": audits}


def print_report_node(state: AgentState) -> dict:
    audits = state.get("releases", [])
    if not audits:
        console.print("[bold red]No audit data to report.[/]")
        return {}

    table = Table(title="Release Quality Audit")
    table.add_column("Tag")
    table.add_column("Words", justify="right")
    table.add_column("Sections")
    table.add_column("Bullets")
    table.add_column("Breaking")
    table.add_column("CVE")
    table.add_column("Score", justify="right")

    for a in audits:
        pct = f"{a['completeness'] * 100:.0f}%"
        table.add_row(
            a["tag"],
            str(a["word_count"]),
            "Yes" if a["has_sections"] else "No",
            "Yes" if a["has_bullets"] else "No",
            "Yes" if a["breaking_mentioned"] else "No",
            "Yes" if a["cve_mentioned"] else "No",
            pct,
        )

    console.print(table)

    avg = sum(a["completeness"] for a in audits) / len(audits)
    console.print(f"\n[bold]Average completeness: {avg * 100:.1f}%[/]")
    if avg < 0.5:
        console.print("[yellow]Suggestion: Add structured sections (## Features, ## Fixes) to improve readability.[/]")
    console.print()

    return {}