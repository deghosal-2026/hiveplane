"""``http_check`` probe (F-19, F-26): GET a URL, read-only, never mutates.

Uses ``httpx`` with a short timeout so a hanging endpoint cannot stall the
re-gate. The observed value is the HTTP status code; a network failure is
reported via :class:`ProbeResult` with ``ok=False`` rather than raised.
"""

from __future__ import annotations

import httpx

from .base import Probe, ProbeKind, ProbeRequest, ProbeResult

DEFAULT_TIMEOUT_S = 5.0


class HttpCheckProbe(Probe):
    """Performs a read-only GET against a URL and records the status."""

    kind: ProbeKind = "http_check"

    def __init__(self, client: httpx.Client | None = None) -> None:
        """Bind an optional client (tests inject a ``MockTransport``)."""
        self._client = client if client is not None else httpx.Client()

    def run(self, request: ProbeRequest) -> ProbeResult:
        """GET ``request.query`` and compare the status to ``expected``.

        Args:
            request: query = URL to GET; expected = required status code.

        Returns:
            The observed status and whether it matches.
        """
        try:
            resp = self._client.get(request.query, timeout=DEFAULT_TIMEOUT_S)
            observed = str(resp.status_code)
            return ProbeResult(
                kind=self.kind,
                query=request.query,
                observed=observed,
                matched=observed == request.expected,
            )
        except httpx.HTTPError as err:
            return ProbeResult(
                kind=self.kind,
                query=request.query,
                observed=f"http error: {err}",
                matched=False,
                ok=False,
            )
