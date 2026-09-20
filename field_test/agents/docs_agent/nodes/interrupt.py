from __future__ import annotations
import io
import json
import logging
from typing import Any

from langgraph.types import interrupt
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from state import AgentState

logger = logging.getLogger(__name__)
console = Console()


def _fmt_panel(title: str, body: str) -> str:
    buf = io.StringIO()
    c = Console(file=buf, force_terminal=False, width=100)
    c.print(Panel(body, title=title, border_style="bold blue"))
    return buf.getvalue()


def interrupt_breaking(state: AgentState) -> dict:
    breaking = state.get("breaking_changes", [])
    index = state.get("current_interrupt_index", 0)

    if index >= len(breaking):
        return {}

    item = breaking[index]
    body = (
        f"Commit: {item['commit_sha']}\n"
        f"Scope: {item.get('scope', 'N/A')}\n"
        f"Footer: {item.get('footer_text', 'N/A')}\n"
        f"Proposed migration: {item.get('proposed_migration', 'N/A')}\n"
        f"Severity: {item.get('severity', 'N/A')}"
    )

    display = _fmt_panel(
        f"Breaking Change {index + 1} of {len(breaking)}",
        body,
    )
    display += (
        "\nEdit migration text (or press Enter to keep as-is):\n"
        "Format: {\"migration\": \"...\", \"severity\": \"migration|breaking|removed\", \"disclosure\": \"immediate|delayed\"}\n> "
    )

    raw = interrupt(display)
    edits: dict[str, Any] = {}
    if raw and isinstance(raw, str) and raw.strip():
        try:
            edits = json.loads(raw.strip())
        except json.JSONDecodeError:
            edits = {"migration": raw.strip()}

    migration = edits.get("migration", item.get("human_migration_text", ""))
    severity = edits.get("severity", item.get("severity", ""))
    disclosure = edits.get("disclosure", item.get("disclosure", "immediate"))

    if migration:
        item["human_migration_text"] = migration
    if severity in ("migration", "breaking", "removed"):
        item["severity"] = severity
    if disclosure in ("immediate", "delayed"):
        item["disclosure"] = disclosure

    logger.info("Breaking change %d/%d reviewed", index + 1, len(breaking))
    return {"current_interrupt_index": index + 1, "breaking_changes": breaking}


def interrupt_security(state: AgentState) -> dict:
    security = state.get("security_fixes", [])
    if not security:
        return {}

    table = Table(title="Security Fixes (Batch)")
    table.add_column("#", justify="right")
    table.add_column("Commit")
    table.add_column("CVE(s)")
    table.add_column("CVSS")
    table.add_column("Advisory")

    for i, fix in enumerate(security):
        table.add_row(
            str(i + 1),
            fix["commit_sha"][:8],
            ", ".join(fix.get("cve_ids", [])) or "None",
            str(fix.get("cvss_score", "N/A")),
            (fix.get("advisory_draft", "") or "")[:50],
        )

    buf = io.StringIO()
    c = Console(file=buf, force_terminal=False, width=100)
    c.print(table)
    display = buf.getvalue()

    display += (
        "\nEdit advisories (JSON dict mapping index to edits) or press Enter to keep as-is:\n"
        "Format: {\"1\": {\"advisory\": \"...\", \"disclosure\": \"immediate|delayed\"}}\n> "
    )

    raw = interrupt(display)
    if raw and isinstance(raw, str) and raw.strip():
        try:
            per_fix: dict[str, dict[str, str]] = json.loads(raw.strip())
            for idx_str, edit in per_fix.items():
                try:
                    idx = int(idx_str) - 1
                    if 0 <= idx < len(security):
                        fix = security[idx]
                        adv = edit.get("advisory", "")
                        disc = edit.get("disclosure", "")
                        if adv:
                            fix["human_advisory_text"] = adv
                        if disc in ("immediate", "delayed"):
                            fix["disclosure"] = disc
                except (ValueError, IndexError):
                    logger.warning("Invalid security fix index: %s", idx_str)
        except json.JSONDecodeError:
            for fix in security:
                if raw.strip():
                    fix["human_advisory_text"] = raw.strip()

    logger.info("Reviewed %d security fixes", len(security))
    return {"security_fixes": security}


def interrupt_semver(state: AgentState) -> dict:
    proposal = state.get("semver_proposal")
    if not proposal:
        return {"semver_approved": False, "semver_cancelled": True}

    body = (
        f"Current version: {proposal['current_version']}\n"
        f"Proposed version: {proposal['proposed_version']}\n"
        f"Bump type: {proposal['bump_type']}\n"
        f"Breaking changes: {proposal['breaking_count']}\n"
        f"Features: {proposal['feature_count']}\n"
        f"Fixes: {proposal['fix_count']}\n"
        f"Justification: {proposal['justification']}\n"
    )

    display = _fmt_panel("Semver Proposal", body)
    display += (
        "\nOptions:\n"
        "  Enter → approve\n"
        "  N → cancel (output without semver bump)\n"
        "  <version> → override with a specific version\n> "
    )

    raw = interrupt(display)
    choice = raw.strip() if raw and isinstance(raw, str) else ""

    if not choice:
        return {"semver_approved": True, "semver_cancelled": False, "semver_override": None}
    if choice.lower() in ("n", "no", "cancel"):
        return {"semver_approved": False, "semver_cancelled": True, "semver_override": None}

    return {"semver_override": choice, "semver_approved": False, "semver_cancelled": False}
