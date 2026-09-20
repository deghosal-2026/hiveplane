"""Replay engine."""

from cauterule.replay.determinism import deterministic_replay
from cauterule.replay.history_check import check_history
from cauterule.replay.loader import load_from_dir, load_from_file
from cauterule.replay.matcher import rule_matches
from cauterule.replay.report import build_evidence_report
from cauterule.replay.scorer import compute_scores
from cauterule.replay.simulator import simulate

__all__ = [
    "build_evidence_report",
    "check_history",
    "compute_scores",
    "deterministic_replay",
    "load_from_dir",
    "load_from_file",
    "rule_matches",
    "simulate",
]
