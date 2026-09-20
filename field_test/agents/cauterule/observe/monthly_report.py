"""Monthly learning report — auto-generated summary of rule health."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.observe.coverage_score import compute_coverage_score
from cauterule.observe.domain_coverage import domain_coverage
from cauterule.observe.leaderboard import get_leaderboard
from cauterule.store.manager import StoreManager


def generate_monthly_report(store: StoreManager) -> str:
    """Return a markdown-formatted monthly learning report."""
    rules = store.list_rules()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    active = [r for r in rules if r.status == "active"]
    total = len(rules)
    active_count = len(active)
    retired = sum(1 for r in rules if r.status == "retired")
    superseded = sum(1 for r in rules if r.status == "superseded")

    total_hits = sum(r.hit_count for r in active)
    avg_conf = sum(r.confidence for r in active) / active_count if active_count > 0 else 0.0
    score = compute_coverage_score(store)
    d_cov = domain_coverage(store)
    lb = get_leaderboard(store)

    lines: list[str] = [
        "# Monthly Learning Report",
        f"**Generated**: {now}",
        "",
        "## Overview",
        f"- **Total Rules**: {total}",
        f"- **Active**: {active_count}",
        f"- **Retired**: {retired}",
        f"- **Superseded**: {superseded}",
        f"- **Total Hits (active)**: {total_hits}",
        f"- **Average Confidence**: {avg_conf:.3f}",
        f"- **Coverage Score**: {score:.4f}",
        "",
        "## Domain Coverage",
    ]

    for domain, cov in sorted(d_cov.items(), key=lambda x: x[1]):
        lines.append(f"- **{domain}**: {cov:.2%}")

    lines.extend(
        [
            "",
            "## Most Prevented Failures",
        ]
    )
    for entry in lb.get("most_prevented", [])[:5]:
        lines.append(f"- Rule {entry['id']}: {entry['count']} failures prevented")

    lines.extend(
        [
            "",
            "## Top Coverage Gaps",
        ]
    )
    for entry in lb.get("top_gaps", [])[:5]:
        lines.append(f"- Rule {entry['id']}: recall={entry['recall']:.2f}")

    lines.append("")
    return "\n".join(lines)
