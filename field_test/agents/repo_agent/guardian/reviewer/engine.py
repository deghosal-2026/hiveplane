from guardian.reviewer.models import ReviewResult, Violation
from collections.abc import Callable
from guardian.reviewer import rules as rule_module
from guardian.policy.models import PolicyConfig
from guardian.detector.models import DetectionResult


BUILTIN_RULES: dict[str, Callable] = {
    "hallucinated-api": rule_module.hallucinated_api,
    "missing-error-handling": rule_module.missing_error_handling,
    "hardcoded-secrets": rule_module.hardcoded_secrets,
}


def compute_risk_level(violations: list[Violation]) -> str:
    if not violations:
        return "low"
    severity_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    max_violation = max(violations, key=lambda v: severity_order.get(v.severity, 0))
    return max_violation.severity


def evaluate(
    detections: list[DetectionResult],
    policy: PolicyConfig,
    diff: str,
) -> ReviewResult:
    violations: list[Violation] = []
    for rule in policy.rules:
        if not rule.enabled:
            continue
        rule_fn = BUILTIN_RULES.get(rule.name)
        if not rule_fn:
            continue
        rule_violations = rule_fn(detections, diff, rule.severity)
        violations.extend(rule_violations)

    risk_level = compute_risk_level(violations)
    summary = f"Found {len(violations)} violation(s), risk level: {risk_level}"
    return ReviewResult(violations=violations, risk_level=risk_level, summary=summary)
