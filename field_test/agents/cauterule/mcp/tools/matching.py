"""MCP tool: ``get_matching_rules`` — find rules matching a task description."""

from __future__ import annotations

from cauterule.models.rule import StandingRule


def get_matching_rules(task: str, rules: list[StandingRule]) -> list[StandingRule]:
    """Return rules whose ``when.trigger`` or ``tags`` match the *task*.

    Simple substring / case-insensitive matching on trigger and tags.
    In a production deployment this would use an embedding-based similarity
    search against the rule index.
    """
    if not task.strip():
        return []
    task_lower = task.lower()
    matched: list[StandingRule] = []
    for rule in rules:
        if rule.status != "active":
            continue
        if task_lower in rule.when.trigger.lower():
            matched.append(rule)
            continue
        for tag in rule.tags:
            if task_lower in tag.lower():
                matched.append(rule)
                break
    return matched
