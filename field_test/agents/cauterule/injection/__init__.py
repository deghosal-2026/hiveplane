"""Rule injection package — match, order, format, budget, preflight."""

from __future__ import annotations

from cauterule.injection.budget import optimize_budget
from cauterule.injection.explainer import explain_rule
from cauterule.injection.fallback import no_match_fallback
from cauterule.injection.formatter import format_injection
from cauterule.injection.matcher import match_rules
from cauterule.injection.ordering import order_by_specificity
from cauterule.injection.portfolio import optimize_portfolio
from cauterule.injection.preflight import preflight
from cauterule.injection.templates import apply_template

__all__ = [
    "apply_template",
    "explain_rule",
    "format_injection",
    "match_rules",
    "no_match_fallback",
    "optimize_budget",
    "optimize_portfolio",
    "order_by_specificity",
    "preflight",
]
