"""Tautology check — detects 'when failing, don't fail'."""

from __future__ import annotations

import re

# #782: word-boundary negation so "note"/"notes" do not match the "not" test.
_NEGATION_RE = re.compile(r"\bnot\b|don'?t")


def check_tautology(trigger: str, directive: str) -> list[str]:
    """Return warnings if trigger and directive are tautological."""
    t = trigger.lower().strip()
    d = directive.lower().strip()
    warnings: list[str] = []
    if t == d and t:
        warnings.append("tautology: trigger and directive are identical")
    if "fail" in t and "fail" in d and _NEGATION_RE.search(d):
        warnings.append("tautology: when failing, don't fail")
    if "error" in t and "error" in d and ("ignore" in d or "skip" in d):
        warnings.append("tautology: when error, ignore error")
    return warnings
