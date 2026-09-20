"""``db_query`` probe (F-19): JSON-fixture-based implementation.

Reads a JSON string containing ``query`` and ``result`` from the ProbeRequest
and returns the fixture result. This lets the C19 field test assert that the
probe kind dispatches correctly without a real database.

In a real deployment, this would connect to a database. The fixture mode
makes the contract testable and the kind dispatchable.
"""

from __future__ import annotations

import json

from .base import Probe, ProbeKind, ProbeRequest, ProbeResult


class DbQueryProbe(Probe):
    """Fixture-based probe — returns a configured result from the request JSON."""

    kind: ProbeKind = "db_query"

    def run(self, request: ProbeRequest) -> ProbeResult:
        try:
            data = json.loads(request.query)
            result = data.get("result", "")
            if not isinstance(result, str):
                result = str(result)
            return ProbeResult(
                kind=self.kind,
                query=request.query,
                observed=result,
                matched=result == request.expected,
            )
        except Exception as exc:
            return ProbeResult(
                kind=self.kind,
                query=request.query,
                observed=f"db_query fixture error: {exc}",
                matched=False,
                ok=False,
            )
