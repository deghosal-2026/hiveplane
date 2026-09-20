"""Direct contradiction detector — same trigger, different directives."""

from __future__ import annotations

from cauterule.models.conflict import ConflictReport
from cauterule.models.rule import StandingRule


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def detect_contradictions(rules: list[StandingRule]) -> list[ConflictReport]:
    """Return ConflictReports for active rules with identical triggers but differing directives."""
    reports: list[ConflictReport] = []
    active = [r for r in rules if r.status == "active"]
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a, b = active[i], active[j]
            if _normalize(a.when.trigger) != _normalize(b.when.trigger):
                continue
            if _normalize(a.do.directive) == _normalize(b.do.directive):
                continue
            reports.append(
                ConflictReport(
                    type="contradiction",
                    rules=(a.id, b.id),
                    trigger=a.when.trigger,
                    resolution=f"Rules '{a.id}' and '{b.id}' fire on the same trigger "
                    f"but direct differently: '{a.do.directive}' vs '{b.do.directive}'",
                )
            )
    return reports
