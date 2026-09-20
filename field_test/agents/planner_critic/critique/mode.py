"""Dual critique mode dispatch (F-10, F-11, PRD §2.5).

One config knob, same engine:

* ``deterministic-first`` (default) — run the free, injection-immune gate
  findings; only invoke the LLM critic on drafts that survive the gates. A
  gate blocker means the model is never spent on a draft that deterministic
  checks already rejected.
* ``llm-every-revision`` — always run the full LLM critique, even on gate-
  blocked drafts, when audit depth justifies the cost.

The dispatch is a pure function of (mode, findings) so it is unit-testable
and deterministic. Injection-safety (§2.5.1) lives at the threshold layer:
a deterministic-gate blocker can never be overridden by the critic regardless
of mode.
"""

from __future__ import annotations

from typing import Literal

from ..types import Finding, Severity

CriticMode = Literal["heuristic-only", "deterministic-first", "llm-every-revision"]

DEFAULT_MODE: CriticMode = "deterministic-first"


def should_invoke_llm(mode: CriticMode, findings: list[Finding]) -> bool:
    """Whether the LLM critic should run for a revision.

    Args:
        mode: The configured critique mode.
        findings: Findings collected by the deterministic gates so far.

    Returns:
        True when the LLM critic should run. In deterministic-first mode, a
        gate blocker short-circuits the model (no spend on a dead draft). In
        llm-every-revision mode the model always runs. In heuristic-only mode
        the model never runs.
    """
    if mode == "heuristic-only":
        return False
    if mode == "llm-every-revision":
        return True
    # deterministic-first: skip the model when a gate already blocked the plan.
    return not any(f.severity is Severity.BLOCKER for f in findings)


def validate_mode(mode: str) -> CriticMode:
    """Coerce a config string to a :class:`CriticMode`.

    Args:
        mode: The raw config value.

    Returns:
        The validated mode.

    Raises:
        ValueError: if the mode is unknown.
    """
    known: dict[str, CriticMode] = {
        "heuristic-only": "heuristic-only",
        "deterministic-first": "deterministic-first",
        "llm-every-revision": "llm-every-revision",
    }
    try:
        return known[mode]
    except KeyError as err:
        raise ValueError(f"unknown critique mode: {mode!r}") from err
