"""Structured matcher — match by trigger, tool, error type, context, tags, taxonomy."""

from __future__ import annotations

from typing import Any

from cauterule.models.rule import StandingRule


def _trigger_matches(task: str, rule: StandingRule) -> bool:
    trigger = rule.when.trigger.lower()
    task_lower = task.lower()
    return bool(trigger and trigger in task_lower)


def _context_matches(task: str, rule: StandingRule, **context: Any) -> bool:
    if not rule.when.context:
        return True
    for ctx_item in rule.when.context:
        ctx_lower = ctx_item.lower()
        found = ctx_lower in task.lower()
        if not found:
            for val in context.values():
                if isinstance(val, str) and ctx_lower in val.lower():
                    found = True
                    break
        if not found:
            return False
    return True


def _tool_matches(rule: StandingRule, **context: Any) -> bool:
    tool = context.get("tool")
    if tool is None:
        return True
    if not rule.when.context:
        # No context = no tool constraint (#503). A context-less rule must
        # not be filtered out merely because a tool filter was passed.
        return True
    tool_lower = str(tool).lower()
    return any(ctx_item.lower() == tool_lower for ctx_item in rule.when.context)


def _error_matches(rule: StandingRule, **context: Any) -> bool:
    # AND-filter contract: every provided filter must pass for the rule to
    # match. A rule matches the error filter iff the trigger or at least
    # one context item appears in the error string (#498).
    error = context.get("error")
    if error is None:
        return True
    error_lower = str(error).lower()
    if rule.when.trigger.lower() in error_lower:
        return True
    return any(ctx_item.lower() in error_lower for ctx_item in rule.when.context)


def _tag_matches(rule: StandingRule, **context: Any) -> bool:
    tags = context.get("tags")
    if tags is None:
        return True
    if not rule.tags:
        return False
    target_tags = set(tags) if isinstance(tags, (list, tuple, set)) else {tags}
    return bool(set(rule.tags) & target_tags)


def _taxonomy_matches(rule: StandingRule, **context: Any) -> bool:
    taxonomy = context.get("taxonomy")
    if taxonomy is None:
        return True
    if rule.taxonomy is None:
        return False
    return bool(taxonomy == rule.taxonomy)


def match_rules(task: str, rules: list[StandingRule], **context: Any) -> list[StandingRule]:
    """Match *rules* against *task* and optional context filters.

    A rule must pass **all** provided context filters (AND logic). If a filter
    is not provided it is skipped.  At minimum the trigger must appear in
    *task* (case-insensitive substring).

    A rule with empty ``when.context`` carries no tool constraint: it matches
    regardless of any ``tool=`` filter (#503).

    Args:
        task: Task description to match against.
        rules: List of promoted standing rules.
        **context: Optional filters — ``tool``, ``error``, ``tags``
            (iterable of str), ``taxonomy`` (str).

    Returns:
        Subset of *rules* that match.
    """
    matched: list[StandingRule] = []
    for rule in rules:
        if not _trigger_matches(task, rule):
            continue
        if not _context_matches(task, rule, **context):
            continue
        if not _tool_matches(rule, **context):
            continue
        if not _error_matches(rule, **context):
            continue
        if not _tag_matches(rule, **context):
            continue
        if not _taxonomy_matches(rule, **context):
            continue
        matched.append(rule)
    # OTEL rule.match spans (#588): best-effort, never blocks injection.
    if matched:
        try:
            from cauterule.integrations.otel import OtelExporter

            exporter = OtelExporter()
            for rule in matched:
                exporter.emit_rule_match(
                    rule.id,
                    trigger=rule.when.trigger,
                    confidence=rule.confidence,
                )
        except Exception:
            pass
    return matched
