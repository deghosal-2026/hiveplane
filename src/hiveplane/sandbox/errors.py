"""Sandbox domain errors."""

from __future__ import annotations


class SandboxError(Exception):
    """Base class for sandbox errors."""


class SandboxNotFoundError(SandboxError):
    """Raised when a sandbox id is unknown."""

    def __init__(self, sandbox_id: str) -> None:
        super().__init__(f"sandbox {sandbox_id!r} not found")
        self.sandbox_id = sandbox_id


class EgressDeniedError(SandboxError):
    """Raised when a host is not permitted by the egress allowlist."""

    def __init__(self, host: str) -> None:
        super().__init__(f"egress to {host!r} is blocked")
        self.host = host
