"""Promotion gate package."""

from cauterule.promotion.auto import auto_promote
from cauterule.promotion.executor import execute_promotion
from cauterule.promotion.human import human_review
from cauterule.promotion.hybrid import hybrid_promote
from cauterule.promotion.thresholds import get_thresholds

__all__ = [
    "auto_promote",
    "execute_promotion",
    "get_thresholds",
    "human_review",
    "hybrid_promote",
]
