"""Eval package — security oracle evaluation infrastructure (M5)."""

from .injection import InjectionResult, InjectionTrap, TrapType, generate_traps
from .injection_harness import injection_summary, run_injection_harness
from .oracle import EvalScorecard, InstanceResult, OracleEvalHarness, save_report

__all__ = [
    "EvalScorecard",
    "InjectionResult",
    "InjectionTrap",
    "InstanceResult",
    "OracleEvalHarness",
    "TrapType",
    "generate_traps",
    "injection_summary",
    "run_injection_harness",
    "save_report",
]
