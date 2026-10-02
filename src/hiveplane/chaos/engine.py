"""Chaos engine: run seeded drills with guardrails and reporting (M48-04..M48-06).

A drill injects a seeded failure and records what the plane did and whether it
recovered. Drills are scoped to a sandbox/tenant and never touch production
without an explicit ``allow_production`` flag and an approving authorizer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from hiveplane.chaos.models import (
    DrillKind,
    DrillRefusedError,
    DrillReport,
    DrillRequest,
    DrillScope,
    DrillVerdict,
)


class DrillOutcome:
    """The observed response of one drill: a description and pass/fail."""

    __slots__ = ("observed", "passed")

    def __init__(self, observed: str, passed: bool) -> None:
        self.observed = observed
        self.passed = passed


class DrillHandler(Protocol):
    """Runs one injected failure and reports the observed response."""

    def __call__(self, request: DrillRequest) -> DrillOutcome: ...


class Authorizer(Protocol):
    """Authorizes production drills (must confirm an admin role)."""

    def __call__(self, request: DrillRequest) -> bool: ...


_INJECTED: dict[DrillKind, str] = {
    DrillKind.KILL_WORKER: "killed a worker mid-run",
    DrillKind.REVOKE_CERT: "revoked a certification mid-flight",
    DrillKind.EXHAUST_BUDGET: "forced budget exhaustion",
    DrillKind.INJECT_TOOL_FAILURE: "injected a tool failure",
}


class ChaosEngine:
    """Runs a catalog of seeded drills and records pass/fail reports."""

    def __init__(
        self,
        drills: dict[DrillKind, DrillHandler],
        *,
        authorizer: Authorizer | None = None,
        audit: Callable[[DrillReport], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        drill_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._drills = dict(drills)
        self._authorizer = authorizer
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._drill_id_factory = drill_id_factory or (lambda: f"drill-{uuid4().hex[:16]}")
        self._reports: dict[str, DrillReport] = {}

    def run(self, request: DrillRequest) -> DrillReport:
        """Run a drill, applying guardrails and recording a pass/fail report."""
        started = self._clock()
        notes: list[str] = []
        if request.production:
            if not request.allow_production:
                return self._refuse(
                    request, started, "production drill requires allow_production"
                )
            if self._authorizer is None or not self._authorizer(request):
                return self._refuse(
                    request,
                    started,
                    "production drill requires an admin authorizer",
                )
        handler = self._drills.get(request.kind)
        if handler is None:
            return self._refuse(request, started, f"no handler for drill {request.kind.value!r}")
        try:
            outcome = handler(request)
        except Exception as exc:
            return self._report(
                request,
                started,
                observed=f"drill raised {exc}",
                verdict=DrillVerdict.FAIL,
                notes=notes,
            )
        verdict = DrillVerdict.PASS if outcome.passed else DrillVerdict.FAIL
        if request.seed is not None:
            notes.append(f"seed={request.seed}")
        return self._report(
            request, started, observed=outcome.observed, verdict=verdict, notes=notes
        )

    def reports(self) -> list[DrillReport]:
        """Return all drill reports, newest first."""
        return sorted(self._reports.values(), key=lambda report: report.started_at, reverse=True)

    def report(self, drill_id: str) -> DrillReport | None:
        """Return a single drill report."""
        return self._reports.get(drill_id)

    def _refuse(self, request: DrillRequest, started: datetime, reason: str) -> DrillReport:
        return self._report(
            request,
            started,
            observed=reason,
            verdict=DrillVerdict.REFUSED,
            notes=[reason],
        )

    def _report(
        self,
        request: DrillRequest,
        started: datetime,
        *,
        observed: str,
        verdict: DrillVerdict,
        notes: list[str],
    ) -> DrillReport:
        report = DrillReport(
            drill_id=self._drill_id_factory(),
            kind=request.kind,
            scope=request.scope,
            scope_ref=request.scope_ref,
            injected=_INJECTED[request.kind],
            observed=observed,
            verdict=verdict,
            started_at=started,
            finished_at=self._clock(),
            notes=notes,
        )
        self._reports[report.drill_id] = report
        if self._audit is not None:
            self._audit(report)
        return report


def ensure_authorized(request: DrillRequest, *, authorized: bool) -> None:
    """Raise :class:`DrillRefusedError` unless a production drill is authorized."""
    if request.production and (not request.allow_production or not authorized):
        raise DrillRefusedError("production drill not authorized")


_ = DrillScope  # exported via package for callers
