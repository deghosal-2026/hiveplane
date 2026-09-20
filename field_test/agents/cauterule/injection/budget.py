"""Context budget optimizer — rank rules, compress, fit token budget."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from cauterule.injection.ordering import order_by_specificity
from cauterule.models.rule import StandingRule

# Rule-cost estimator: a rule's token footprint. Defaults to a documented
# ``words * 2`` heuristic (+10 formatting overhead) which is a conservative
# upper bound for prose directives and error codes. Callers may inject a
# real tokenizer via ``optimize_budget(token_estimator=...)`` (#522).
TokenEstimator = Callable[[StandingRule], int]


def _rule_token_estimate(rule: StandingRule) -> int:
    """Estimate the token cost of a rule using ``words * 2`` (#522).

    The factor-2 accounts for typical subword tokenization of code-heavy
    directives (paths, error codes, camelCase). Exact fidelity is not
    required: the optimizer only needs consistent relative costs to rank
    and fit rules within the budget.
    """
    total = len(rule.when.trigger.split()) * 2
    total += len(rule.do.directive.split()) * 2
    total += sum(len(c.split()) * 2 for c in rule.when.context)
    if rule.do.because:
        total += len(rule.do.because.split()) * 2
    total += len(rule.tags) * 2
    if rule.taxonomy:
        total += 3
    total += 10  # formatting overhead
    return total


def _compress_rule(rule: StandingRule) -> StandingRule:
    """Return a compressed version with trigger + directive only.

    Strips rendering weight (context, because, tags, taxonomy) but
    preserves identity/observability metadata (``hit_count``,
    ``last_match``, ``pack``, ``template``, ``retired_*``,
    ``superseded_by``) via :func:`dataclasses.replace` (#522).
    """
    return replace(
        rule,
        when=replace(rule.when, context=()),
        do=replace(rule.do, because=None),
        tags=(),
        taxonomy=None,
    )


def _one_liner(rule: StandingRule) -> StandingRule:
    """Return a one-liner version: 'When {trigger} → Do {directive}'."""
    one_line = f"When {rule.when.trigger} → Do {rule.do.directive}"
    return _compress_rule(replace(rule, do=replace(rule.do, directive=one_line)))


def _value_density(rule: StandingRule, cost: int) -> float:
    """Value per token: (hits + 1) * confidence / cost (#522).

    Promotes rules that actually prevent failures (high ``hit_count``)
    over verbose, never-fired rules under a tight budget.
    """
    if cost <= 0:
        return 0.0
    return ((rule.hit_count + 1) * rule.confidence) / cost


def _best_value_density(rule: StandingRule, est: TokenEstimator) -> float:
    """Value density of the *smallest* representation (code-review).

    A verbose high-value rule whose compressed form is cheap should rank
    by the compressed density, not the full-cost density -- otherwise a
    low-value compact rule that fits the budget exactly would crowd it
    out even though the high-value rule would fit compressed.
    """
    best_cost = min(
        est(_compress_rule(rule)),
        est(_one_liner(rule)),
        est(rule),
    )
    return _value_density(rule, best_cost)


def optimize_budget(
    rules: list[StandingRule],
    max_tokens: int = 4096,
    token_estimator: TokenEstimator = _rule_token_estimate,
) -> list[StandingRule]:
    """Select and order rules to fit within *max_tokens*.

    Rules are ranked by value density (hits x confidence per token) with
    specificity as the tiebreaker; then greedily selected until the
    estimated token budget is exhausted. Rules that do not fit are
    compressed to trigger + directive only. If still too tight after
    compression, they are rendered as a one-liner. If still too tight,
    the rule is dropped.

    Args:
        rules: List of standing rules to consider.
        max_tokens: Maximum allowed token count (default 4096).
        token_estimator: Optional cost function (defaults to the documented
            ``words * 2`` heuristic).

    Returns:
        Subset of *rules* that fits within the budget, ranked by value.
    """
    ranked = order_by_specificity(rules)
    ranked.sort(key=lambda r: _best_value_density(r, token_estimator), reverse=True)

    selected: list[StandingRule] = []
    running_total = 0
    for rule in ranked:
        cost = token_estimator(rule)
        if running_total + cost <= max_tokens:
            selected.append(rule)
            running_total += cost
            continue
        compressed = _compress_rule(rule)
        cost = token_estimator(compressed)
        if running_total + cost <= max_tokens:
            selected.append(compressed)
            running_total += cost
            continue
        mini = _one_liner(rule)
        cost = token_estimator(mini)
        if running_total + cost <= max_tokens:
            selected.append(mini)
            running_total += cost
    return selected
