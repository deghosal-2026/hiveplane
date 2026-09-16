"""Network egress allowlist enforcement (D11)."""

from __future__ import annotations

from hiveplane.core.sandbox import (
    CLOUD_METADATA_ENDPOINTS,
    EgressMode,
    EgressSpec,
)
from hiveplane.sandbox.errors import EgressDeniedError


class EgressGuard:
    """Decides whether an outbound host is permitted for a sandbox."""

    def __init__(self, spec: EgressSpec) -> None:
        self._spec = spec

    def check(self, host: str) -> None:
        """Raise EgressDeniedError when the host is not permitted."""
        if host in CLOUD_METADATA_ENDPOINTS or host in self._spec.deny:
            raise EgressDeniedError(host)
        if self._spec.mode is EgressMode.NONE:
            raise EgressDeniedError(host)
        if self._spec.mode is EgressMode.OPEN:
            return
        if host not in self._spec.allow:
            raise EgressDeniedError(host)
