"""Export to .windsurfrules format.

.windsurfrules is a Markdown file Windsurf reads to apply standing rules.
"""

from __future__ import annotations

from cauterule.models.rule import StandingRule

from .redaction import redact_export, sanitize_inline


def export(rules: list[StandingRule], include_retired: bool = False) -> str:
    """Format *rules* as a .windsurfrules Markdown string.

    Only active rules are exported unless *include_retired* is ``True``.
    """
    if not include_retired:
        rules = [r for r in rules if r.status == "active"]
    lines: list[str] = ["## Standing Rules", "", "These rules are active for this session:", ""]
    for i, rule in enumerate(rules, 1):
        trigger = sanitize_inline(redact_export(rule.when.trigger))
        directive = sanitize_inline(redact_export(rule.do.directive))
        lines.append(f"{i}. When `{trigger}` then **{directive}**")
        if rule.do.because:
            because = sanitize_inline(redact_export(rule.do.because))
            lines.append(f"   - Rationale: {because}")
        if rule.tags:
            lines.append(f"   - Categories: {', '.join(rule.tags)}")
        lines.append("")
    return "\n".join(lines)
