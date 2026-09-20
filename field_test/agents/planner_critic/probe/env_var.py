"""``env_var`` probe (F-19): read an environment variable, never write one."""

from __future__ import annotations

import os

from .base import Probe, ProbeKind, ProbeRequest, ProbeResult


class EnvVarProbe(Probe):
    """Reads an environment variable and reports it against the expected value."""

    kind: ProbeKind = "env_var"

    def run(self, request: ProbeRequest) -> ProbeResult:
        """Observe the variable (read-only) and compare to ``expected``.

        Args:
            request: query = variable name; expected = the required value.

        Returns:
            The observed value and whether it matches.
        """
        observed = os.environ.get(request.query, "")
        return ProbeResult(
            kind=self.kind,
            query=request.query,
            observed=observed,
            matched=observed == request.expected,
        )
