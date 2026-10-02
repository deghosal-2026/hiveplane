"""Network egress allowlist enforcement (D11, extended by M39-05).

Thin backward-compatible wrapper over :class:`hiveplane.defense.egress.EgressPolicy`,
which adds ports and wildcard hosts.
"""

from __future__ import annotations

from hiveplane.core.sandbox import EgressRule, EgressSpec
from hiveplane.defense.egress import EgressPolicy
from hiveplane.sandbox.errors import EgressDeniedError


class EgressGuard:
    """Decides whether an outbound host/port is permitted for a sandbox."""

    def __init__(self, spec: EgressSpec) -> None:
        self._policy = EgressPolicy(
            mode=spec.mode,
            allow=[EgressRule(host=host) for host in spec.allow],
            deny=[EgressRule(host=host) for host in spec.deny],
        )

    def check(self, host: str, port: int | None = None) -> None:
        """Raise EgressDeniedError when the host/port is not permitted."""
        if not self._policy.check(host, port).allowed:
            raise EgressDeniedError(host)
