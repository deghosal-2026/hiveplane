"""Export to .cursorrules format.

.cursorrules is a Markdown file Cursor reads to apply standing rules.
Each rule is rendered as a bullet with its trigger and directive.
"""

from __future__ import annotations

from cauterule.models.rule import StandingRule

from .redaction import redact_export, sanitize_inline


def export(rules: list[StandingRule], include_retired: bool = False) -> str:
    """Format *rules* as a .cursorrules Markdown string.

    Only active rules are exported unless *include_retired* is ``True``.
    """
    if not include_retired:
        rules = [r for r in rules if r.status == "active"]
    lines: list[str] = ["# Standing Rules", "", "You MUST follow these standing rules:", ""]
    for i, rule in enumerate(rules, 1):
        trigger = sanitize_inline(redact_export(rule.when.trigger))
        directive = sanitize_inline(redact_export(rule.do.directive))
        lines.append(f"{i}. **When** {trigger} → **Do** {directive}")
        if rule.do.because:
            because = sanitize_inline(redact_export(rule.do.because))
            lines.append(f"   - Because: {because}")
        if rule.tags:
            lines.append(f"   - Tags: {', '.join(rule.tags)}")
        lines.append("")
    return "\n".join(lines)
