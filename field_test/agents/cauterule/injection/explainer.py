"""Rule explanations — LLM generates human-readable explanation."""

from __future__ import annotations

from cauterule.models.rule import StandingRule


def explain_rule(rule: StandingRule) -> str:
    """Generate a human-readable explanation for *rule*.

    In v0.1.0 this is a template-based explanation.  Future versions will
    call an LLM to produce richer natural-language explanations.

    Args:
        rule: The standing rule to explain.

    Returns:
        A human-readable string explaining what the rule does and why.
    """
    parts: list[str] = [f"Rule **{rule.id}**"]
    parts.append(f'  Trigger: When you encounter "{rule.when.trigger}"')
    if rule.when.context:
        ctx = ", ".join(rule.when.context)
        parts.append(f"  Context: especially when context includes ({ctx})")
    parts.append(f"  Action: {rule.do.directive}")
    if rule.do.because:
        parts.append(f"  Rationale: {rule.do.because}")
    parts.append(f"  Confidence: {rule.confidence:.0%}")
    parts.append(f"  Status: {rule.status}")
    if rule.tags:
        parts.append(f"  Tags: {', '.join(rule.tags)}")
    if rule.taxonomy:
        parts.append(f"  Category: {rule.taxonomy}")
    return "\n".join(parts)
