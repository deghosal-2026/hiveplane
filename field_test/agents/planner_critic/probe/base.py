"""The EnvProbe protocol (F-19, F-26, PRD §2.8): read-only environment probes.

A probe grounds a precondition in *live state* — the world may have moved
since planning. Probes are **read-only by contract**: they observe, never
mutate. Their results are recorded so execution-time re-gating and forensics
can reconstruct what was true when a step ran. Deterministic gates never
depend on a probe (they run before any I/O); probes enrich execution-time
re-gating only.

The probe layer keeps the engine hermetic: every built-in is implemented with
the stdlib, and ``db_query``/``deploy_status`` are deliberate stubs in M2 so
the contract is testable without a database or a deploy orchestrator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProbeKind = Literal["env_var", "db_query", "http_check", "deploy_status"]


class ProbeRequest(BaseModel):
    """A probe to run against live state (mirrors the schema EnvProbe).

    Attributes:
        kind: Which probe implementation to run.
        query: The probe's input (env var name, SQL, URL, deployment id).
        expected: The value the caller expects; a probe reports match/not.
    """

    model_config = ConfigDict(frozen=True)

    kind: ProbeKind
    query: str = Field(min_length=1)
    expected: str = Field(min_length=1)


class ProbeResult(BaseModel):
    """The outcome of running a probe.

    Attributes:
        kind: The probe kind that ran.
        query: The query that was probed.
        observed: The value observed (or an error message).
        matched: Whether ``observed`` satisfies ``expected``.
        ok: Whether the probe itself ran without error.
        run_at: When the probe ran (UTC).
    """

    model_config = ConfigDict(frozen=True)

    kind: ProbeKind
    query: str
    observed: str
    matched: bool
    ok: bool = True
    run_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def record(self) -> dict[str, object]:
        """JSON-friendly snapshot for the execution trace."""
        return self.model_dump(mode="json")


class ProbeError(Exception):
    """A probe could not run (its result is recorded, not thrown silently)."""


class Probe(ABC):
    """A read-only probe implementation.

    Subclasses implement :meth:`run` for one :class:`ProbeKind`. Implementations
    must never mutate state; a failure to run is reported via :class:`ProbeResult`
    with ``ok=False`` (recorded), not raised into the planning path.
    """

    kind: ProbeKind

    @abstractmethod
    def run(self, request: ProbeRequest) -> ProbeResult:
        """Run the probe against live state and return the result.

        Args:
            request: What to probe and the expected value.

        Returns:
            The observed result, recorded for the execution trace.
        """


def run_probe(request: ProbeRequest) -> ProbeResult:
    """Dispatch a probe request to the matching built-in implementation.

    Args:
        request: The probe request to run.

    Returns:
        The probe result. Unknown kinds or runtime failures return a recorded
        result with ``ok=False`` — a probe never raises.
    """
    impls: dict[str, Probe] = {p.kind: p for p in BUILTIN_PROBES}
    impl = impls.get(request.kind)
    if impl is None:
        return ProbeResult.model_construct(
            kind=request.kind,
            query=request.query,
            observed=f"no probe implementation for kind '{request.kind}'",
            matched=False,
            ok=False,
        )
    try:
        return impl.run(request)
    except ProbeError as err:
        return ProbeResult.model_construct(
            kind=request.kind,
            query=request.query,
            observed=str(err),
            matched=False,
            ok=False,
        )


# Built-in probes are imported lazily to avoid circular imports at package load.
BUILTIN_PROBES: list[Probe] = []


def _register_impl(probe: Probe) -> None:
    """Register a built-in probe implementation."""
    BUILTIN_PROBES.append(probe)


from .db_query import DbQueryProbe  # noqa: E402
from .deploy_status import DeployStatusProbe  # noqa: E402
from .env_var import EnvVarProbe  # noqa: E402
from .http_check import HttpCheckProbe  # noqa: E402

_register_impl(EnvVarProbe())
_register_impl(HttpCheckProbe())
_register_impl(DbQueryProbe())
_register_impl(DeployStatusProbe())
