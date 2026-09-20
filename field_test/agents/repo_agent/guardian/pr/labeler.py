"""Risk level labeler — maps risk level to guardian:* labels."""

from guardian.pr.client import GitHubClient

RISK_LABELS: dict[str, str] = {
    "low": "guardian:low",
    "medium": "guardian:medium",
    "high": "guardian:high",
    "critical": "guardian:critical",
}


def apply_risk_label(client: GitHubClient, pr_number: int, risk_level: str) -> None:
    for label in RISK_LABELS.values():
        try:
            client.remove_label(pr_number, label)
        except Exception:
            pass
    client.add_label(pr_number, RISK_LABELS[risk_level])


def compute_risk_level(violations: list) -> str:
    if not violations:
        return "low"
    severity_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    max_v = max(violations, key=lambda v: severity_order.get(v.severity, 0))
    return max_v.severity
