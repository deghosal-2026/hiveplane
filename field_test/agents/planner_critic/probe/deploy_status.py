"""``deploy_status`` probe (F-19): JSON-fixture-based implementation.

Reads a JSON string containing ``service`` and ``status`` from the
ProbeRequest and returns the fixture status. This lets the C19 field test
assert that the probe kind dispatches correctly without a real orchestrator.
"""

from __future__ import annotations

import json

from .base import Probe, ProbeKind, ProbeRequest, ProbeResult


class DeployStatusProbe(Probe):
    """Fixture-based probe — returns a configured status from the request JSON."""

    kind: ProbeKind = "deploy_status"

    def run(self, request: ProbeRequest) -> ProbeResult:
        try:
            data = json.loads(request.query)
            status = data.get("status", "")
            if not isinstance(status, str):
                status = str(status)
            return ProbeResult(
                kind=self.kind,
                query=request.query,
                observed=status,
                matched=status == request.expected,
            )
        except Exception as exc:
            return ProbeResult(
                kind=self.kind,
                query=request.query,
                observed=f"deploy_status fixture error: {exc}",
                matched=False,
                ok=False,
            )
