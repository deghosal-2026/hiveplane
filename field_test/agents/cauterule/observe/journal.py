"""Learning journal — auto-generated markdown log of all rules."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.store.manager import StoreManager


def generate_journal(store: StoreManager) -> str:
    """Return a markdown-formatted learning journal covering all rules."""
    rules = store.list_rules()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: list[str] = [f"# Learning Journal — {now}", ""]

    for r in sorted(rules, key=lambda x: x.promoted_at, reverse=True):
        lines.append(f"## Rule {r.id}")
        lines.append(f"- **Trigger**: {r.when.trigger}")
        lines.append(f"- **Directive**: {r.do.directive}")
        lines.append(f"- **Confidence**: {r.confidence}")
        lines.append(f"- **Status**: {r.status}")
        lines.append(f"- **Hit Count**: {r.hit_count}")
        lines.append(f"- **Promoted At**: {r.promoted_at}")
        if r.last_match:
            lines.append(f"- **Last Match**: {r.last_match}")
        if r.tags:
            lines.append(f"- **Tags**: {', '.join(r.tags)}")
        ev = r.provenance.replay_evidence
        if ev is not None:
            lines.append(f"- **Failures Prevented**: {len(ev.failures_prevented)}")
            lines.append(f"- **Successes Broken**: {len(ev.successes_broken)}")
            lines.append(f"- **Precision**: {ev.precision}")
            lines.append(f"- **Recall**: {ev.recall}")
        lines.append("")

    return "\n".join(lines)
