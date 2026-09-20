"""Export to aider.conf.yml format.

aider.conf.yml is a YAML configuration file that aider reads for
convention/rule settings.
"""

from __future__ import annotations

import yaml

from cauterule.models.rule import StandingRule

from .redaction import redact_export, sanitize_inline


def _yaml_scalar(text: str) -> str:
    """Quote/escape *text* as a single safe YAML scalar (#798)."""
    return yaml.safe_dump(text, default_style='"', allow_unicode=True).strip()


def export(rules: list[StandingRule], include_retired: bool = False) -> str:
    """Format *rules* as an aider.conf.yml YAML string.

    Only active rules are exported unless *include_retired* is ``True``.
    """
    if not include_retired:
        rules = [r for r in rules if r.status == "active"]
    if not rules:
        return "# No rules\nrules: []\n"

    lines: list[str] = ["# Standing rules for aider", "rules:"]
    for rule in rules:
        trigger = sanitize_inline(redact_export(rule.when.trigger))
        directive = sanitize_inline(redact_export(rule.do.directive))
        lines.append(f"  - when: {_yaml_scalar(trigger)}")
        lines.append(f"    do: {_yaml_scalar(directive)}")
        if rule.do.because:
            because = sanitize_inline(redact_export(rule.do.because))
            lines.append(f"    because: {_yaml_scalar(because)}")
        if rule.tags:
            tags_str = ", ".join(_yaml_scalar(t) for t in rule.tags)
            lines.append(f"    tags: [{tags_str}]")
    lines.append("")
    return "\n".join(lines)
