"""Check Run output generator — builds the GitHub Checks tab summary."""

from guardian.reviewer.models import ReviewResult


def generate_check_run_output(review: ReviewResult, stats: dict) -> dict:
    lines: list[str] = []
    for v in review.violations:
        loc = f"{v.file_path}:{v.line}" if v.line else v.file_path
        lines.append(f"- **[{v.severity.upper()}]** {v.rule_name} at {loc}: {v.explanation}")
    text_body = "\n".join(lines) if lines else "No violations found."

    summary = (
        f"**Risk Level:** {review.risk_level.upper()}\n\n"
        f"**AI Code:** {stats.get('ai_file_count', 0)}/{stats.get('total_file_count', 0)} files "
        f"({stats.get('ai_pct', 0)}%)\n\n"
        f"**Violations:** {len(review.violations)}\n\n"
        f"**FPR:** {stats.get('fpr', 'N/A')}"
    )

    return {
        "title": "AI Code Guardian — Review Complete",
        "summary": summary,
        "text": text_body,
    }


def determine_conclusion(review: ReviewResult, mode: str) -> str:
    if mode == "advisory":
        return "neutral"
    if review.risk_level == "critical":
        return "failure"
    return "success"
