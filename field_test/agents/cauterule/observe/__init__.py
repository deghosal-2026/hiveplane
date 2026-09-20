"""Observability — hit counters, coverage scores, learning journal, reports."""

from __future__ import annotations

from cauterule.observe.class_coverage import class_coverage
from cauterule.observe.coverage_frontier import suggest_next_frontier
from cauterule.observe.coverage_gap import find_coverage_gaps
from cauterule.observe.coverage_score import compute_coverage_score
from cauterule.observe.domain_coverage import domain_coverage
from cauterule.observe.hits import get_hit_counts
from cauterule.observe.journal import generate_journal
from cauterule.observe.leaderboard import get_leaderboard
from cauterule.observe.monthly_report import generate_monthly_report
from cauterule.observe.timestamps import update_last_match

__all__ = [
    "class_coverage",
    "compute_coverage_score",
    "domain_coverage",
    "find_coverage_gaps",
    "generate_journal",
    "generate_monthly_report",
    "get_hit_counts",
    "get_leaderboard",
    "suggest_next_frontier",
    "update_last_match",
]
