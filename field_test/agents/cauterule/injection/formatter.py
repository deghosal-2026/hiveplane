"""Injection formatter — produces clean markdown block."""

from __future__ import annotations

from cauterule.models.rule import StandingRule


def _safe(text: str) -> str:
    """Neutralize markdown structure in untrusted rule text (#508).

    Rule trigger/directive content is data, not instructions: backticks
    are stripped so fences can't break, and lines starting with ``#``
    are escaped so a rule can't forge a ``### Rule N:`` header.
    """
    cleaned = text.replace("`", "'")
    lines = [
        f"\\{line}" if line.lstrip().startswith("#") else line for line in cleaned.splitlines()
    ]
    return "\n".join(lines)


def _format_single(rule: StandingRule, index: int) -> str:
    lines: list[str] = []
    lines.append(f"### Rule {index}: {rule.id}")
    lines.append(f"- **When**: {_safe(rule.when.trigger)}")
    if rule.when.context:
        lines.append(f"- **Context**: {_safe(', '.join(rule.when.context))}")
    lines.append(f"- **Do**: {_safe(rule.do.directive)}")
    if rule.do.because:
        lines.append(f"- **Because**: {_safe(rule.do.because)}")
    if rule.tags:
        lines.append(f"- **Tags**: {_safe(', '.join(rule.tags))}")
    if rule.taxonomy:
        lines.append(f"- **Taxonomy**: {_safe(rule.taxonomy)}")
    if rule.provenance.source_trajectory:
        lines.append(
            f"- **Source**: Learned from {_safe(rule.provenance.source_trajectory)} ({rule.promoted_at})"
        )
    if rule.provenance.replay_evidence:
        ev = rule.provenance.replay_evidence
        n = len(ev.failures_prevented)
        m = len(ev.successes_broken)
        pct = f"{ev.precision:.0%}" if ev.precision > 0 else "N/A"
        lines.append(f"- **Evidence**: prevented {n}, broke {m} (precision {pct})")
    lines.append(f"- **Confidence**: {rule.confidence}")
    lines.append("")
    return "\n".join(lines)


def format_injection(rules: list[StandingRule]) -> str:
    """Produce a clean markdown block of active rules for injection.

    Args:
        rules: List of standing rules to format.

    Returns:
        A markdown string with each rule rendered as a sub-section.
        Returns ``"<!-- no active rules -->"`` when *rules* is empty.
    """
    if not rules:
        return "<!-- no active rules -->"
    blocks = [_format_single(r, i + 1) for i, r in enumerate(rules)]
    return "\n".join(blocks).strip()
