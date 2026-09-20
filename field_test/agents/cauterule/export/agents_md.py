"""Export to AGENTS.md format.

AGENTS.md is a Markdown convention file that agents read to understand
project-level standing rules (similar to CLAUDE.md).
"""

from __future__ import annotations

from cauterule.models.rule import StandingRule

from .redaction import redact_export, sanitize_inline


def export(rules: list[StandingRule], include_retired: bool = False) -> str:
    """Format *rules* as an AGENTS.md Markdown string.

    Only active rules are exported unless *include_retired* is ``True``.
    """
    if not include_retired:
        rules = [r for r in rules if r.status == "active"]
    lines: list[str] = ["# Standing Rules", "", "The following standing rules apply:", ""]
    for i, rule in enumerate(rules, 1):
        trigger = sanitize_inline(redact_export(rule.when.trigger))
        directive = sanitize_inline(redact_export(rule.do.directive))
        lines.append(f"## Rule {i}")
        lines.append("")
        lines.append(f"- **When:** {trigger}")
        lines.append(f"- **Do:** {directive}")
        if rule.do.because:
            because = sanitize_inline(redact_export(rule.do.because))
            lines.append(f"- **Because:** {because}")
        if rule.tags:
            lines.append(f"- **Tags:** {', '.join(rule.tags)}")
        if rule.taxonomy:
            lines.append(f"- **Taxonomy:** {rule.taxonomy}")
        lines.append("")
    return "\n".join(lines)
