"""PR comment generator — builds the markdown comment body."""

from guardian.reviewer.models import ReviewResult

SEVERITY_EMOJI = {
    "critical": "🚨",
    "high": "⚠️",
    "medium": "⚡",
    "low": "ℹ️",
}


def generate_comment(
    review: ReviewResult,
    mode: str,
    policy_url: str | None = None,
) -> str:
    parts: list[str] = []

    violation_count = len(review.violations)
    emoji = SEVERITY_EMOJI.get(review.risk_level, "ℹ️")
    parts.append(
        f"🤖 **AI Code Guardian — Review Summary**\n\n"
        f"{emoji} **Risk Level:** {review.risk_level.upper()} "
        f"({violation_count} violation(s))\n"
    )

    if violation_count == 0:
        parts.append("✅ No violations found.\n")
    else:
        sorted_v = sorted(
            review.violations,
            key=lambda v: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(v.severity, 4),
        )

        display = sorted_v[:20]
        remaining = sorted_v[20:]

        for v in display:
            sev_emoji = SEVERITY_EMOJI.get(v.severity, "ℹ️")
            loc = f" — `{v.file_path}:{v.line}`" if v.line else f" — `{v.file_path}`"
            parts.append(
                f"<details>\n"
                f"<summary>{sev_emoji} Rule: {v.rule_name}{loc}</summary>\n\n"
                f"**Pattern:** `{v.pattern_found}`\n\n"
                f"{v.explanation}\n\n"
                f"**Recommendation:** {v.remediation}\n"
            )
            if v.governance_url:
                parts.append(f"[Learn more →]({v.governance_url})\n")
            parts.append("</details>\n")

        if remaining:
            parts.append(
                f"<details>\n"
                f"<summary>📋 {len(remaining)} additional violation(s) "
                f"(showing top {len(display)} of {violation_count})</summary>\n\n"
            )
            for v in remaining:
                sev_emoji = SEVERITY_EMOJI.get(v.severity, "ℹ️")
                loc = f"`{v.file_path}:{v.line}`" if v.line else f"`{v.file_path}`"
                parts.append(f"- {sev_emoji} **{v.rule_name}** at {loc}: {v.explanation}\n")
            parts.append("</details>\n")

    mode_desc = (
        "This is an advisory review. No code is blocked."
        if mode == "advisory"
        else "Enforcement mode is active. Critical violations will fail the check."
    )
    parts.append(
        f"---\n"
        f"ℹ️ **{mode.title()} mode** — {mode_desc}\n\n"
        f"Feedback? React 👍 (accurate) or 👎 (false positive) to this comment."
    )

    return "\n".join(parts)
