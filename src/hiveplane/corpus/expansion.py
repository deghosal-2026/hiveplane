"""Scheduled corpus expansion from reviewed feedback candidates (M55-05)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from hiveplane.certification.models import BenchmarkCorpus

#: The integrator adds only reviewed (approved) candidates to a corpus (M36).
Integrator = Callable[[str, BenchmarkCorpus], BenchmarkCorpus]


class CorpusExpansionService:
    """Drives periodic corpus expansion on a per-workload cadence.

    Expansion reuses the M36 corpus integrator, which only ever adds *approved*
    feedback candidates; this service decides *when* to run it. It is pure and
    clock-injected, mirroring the drift/probe schedulers, so an external timer
    (or the operator) can call :meth:`run_due`.
    """

    def __init__(
        self,
        integrator: Integrator,
        *,
        interval_seconds: int = 7 * 86400,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._integrator = integrator
        self._interval = interval_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last: dict[str, datetime] = {}

    def due(self, workload: str, *, at: datetime | None = None) -> bool:
        """Return whether an expansion is due for a workload."""
        now = at or self._clock()
        last = self._last.get(workload)
        return last is None or now - last >= timedelta(seconds=self._interval)

    def expand(
        self, workload: str, base: BenchmarkCorpus, *, at: datetime | None = None
    ) -> BenchmarkCorpus:
        """Integrate reviewed candidates into the corpus and record the run time."""
        now = at or self._clock()
        self._last[workload] = now
        return self._integrator(workload, base)

    def run_due(
        self, workload: str, base: BenchmarkCorpus, *, at: datetime | None = None
    ) -> BenchmarkCorpus | None:
        """Expand only when due; return the (possibly unchanged) corpus."""
        if not self.due(workload, at=at):
            return None
        return self.expand(workload, base, at=at)
