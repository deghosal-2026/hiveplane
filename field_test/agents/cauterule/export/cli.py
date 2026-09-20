"""Export/import CLI helpers.

Provides top-level functions that dispatch to format-specific exporters
and imports from convention files and chat history.
"""

from __future__ import annotations

from pathlib import Path

from cauterule.export.agents_md import export as export_agents_md
from cauterule.export.aider import export as export_aider
from cauterule.export.claude_md import export as export_claude_md
from cauterule.export.cursorrules import export as export_cursorrules
from cauterule.export.generic import export_json, export_markdown
from cauterule.export.windsurf import export as export_windsurf
from cauterule.import_.chat_history import import_chat_history
from cauterule.import_.conventions import import_conventions
from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import StandingRule


def export_rules(rules: list[StandingRule], fmt: str, include_retired: bool = False) -> str:
    """Export *rules* in the requested *fmt*.

    Only active rules are exported unless *include_retired* is ``True``.

    Supported formats: ``cursorrules``, ``claude``, ``agents``, ``windsurf``,
    ``aider``, ``markdown``, ``json``.
    """
    if not include_retired:
        rules = [r for r in rules if r.status == "active"]
    exporters = {
        "cursorrules": export_cursorrules,
        "claude": export_claude_md,
        "agents": export_agents_md,
        "windsurf": export_windsurf,
        "aider": export_aider,
        "markdown": export_markdown,
        "json": export_json,
    }
    exporter = exporters.get(fmt)
    if exporter is None:
        msg = f"Unknown export format: {fmt!r}. Supported: {', '.join(sorted(exporters))}"
        raise ValueError(msg)
    return exporter(rules, include_retired=include_retired)


def import_rules(path: str) -> list[CandidateRule]:
    """Import rules from a file at *path*.

    Detects the format by filename:
      - ``.cursorrules``, ``CLAUDE.md``, ``AGENTS.md`` → convention import
      - else → chat history import
    """
    p = Path(path)
    name = p.name
    if name in (".cursorrules", "CLAUDE.md", "AGENTS.md", "claude.md", "agents.md"):
        return import_conventions(str(p))
    return import_chat_history(str(p))
