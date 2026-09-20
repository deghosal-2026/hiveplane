"""Conflict detection and consolidation package."""

from cauterule.conflict.consolidation import consolidate
from cauterule.conflict.contradiction import detect_contradictions
from cauterule.conflict.duplicates import detect_duplicates
from cauterule.conflict.overlap import detect_overlaps
from cauterule.conflict.specificity import score_specificity

__all__ = [
    "consolidate",
    "detect_contradictions",
    "detect_duplicates",
    "detect_overlaps",
    "score_specificity",
]
